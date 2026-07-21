"""Összetevők (Components) — CRUD + temporal price change + history + enable/disable.

Price changes go through the append-only COMPONENT_PRICES mechanism (§3.4):
closing the open row and inserting a new one. Historical rows are never mutated.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import Component, ComponentPrice, Group
from app.routers._helpers import decimal_hu, get_or_404, return_to, see_other, see_other_back
from app.services.pricing import pick_effective, prices_for
from app.templating import templates

router = APIRouter()


def _groups(session: Session) -> list[Group]:
    return list(session.scalars(select(Group).order_by(Group.sort_order, Group.name)))


@router.get("/components", response_class=HTMLResponse)
def list_components(
    request: Request,
    q: str = "",
    group_id: str = "",
    only_active: bool = False,
    session: Session = Depends(get_session),
):
    gid = int(group_id) if group_id.strip().isdigit() else None
    stmt = select(Component).options(selectinload(Component.group))
    if q.strip():
        stmt = stmt.where(func.lower(Component.name).like(f"%{q.strip().lower()}%"))
    if gid:
        stmt = stmt.where(Component.group_id == gid)
    if only_active:
        stmt = stmt.where(Component.active.is_(True))
    stmt = stmt.order_by(Component.name)
    components = list(session.scalars(stmt))
    now = dt.datetime.now(dt.UTC)
    # One query for all prices, then pick the current row per component (no N+1).
    price_rows = prices_for(session, [c.id for c in components])
    prices = {c.id: pick_effective(price_rows.get(c.id, []), now) for c in components}
    ctx = {
        "components": components,
        "prices": prices,
        "groups": _groups(session),
        "q": q,
        "group_id": gid,
        "only_active": only_active,
        "active_nav": "components",
    }
    tmpl = "components/_rows.html" if request.headers.get("HX-Request") else "components/list.html"
    return templates.TemplateResponse(request, tmpl, ctx)


@router.get("/components/detail/{component_id:int}", response_class=HTMLResponse)
def component_detail(component_id: int, request: Request, session: Session = Depends(get_session)):
    component = get_or_404(session, Component, component_id)
    # history is DESC by effective_date; pick_effective wants ASC → reverse once.
    history = list(
        session.scalars(
            select(ComponentPrice)
            .where(ComponentPrice.component_id == component_id)
            .order_by(ComponentPrice.effective_date.desc())
        )
    )
    now = dt.datetime.now(dt.UTC)
    current = pick_effective(list(reversed(history)), now)
    return templates.TemplateResponse(
        request,
        "components/_detail.html",
        {"c": component, "history": history, "current": current},
    )


@router.get("/components/new", response_class=HTMLResponse)
def new_component_form(
    request: Request, group_id: int | None = None, session: Session = Depends(get_session)
):
    return templates.TemplateResponse(
        request,
        "components/form.html",
        {
            "c": None,
            "groups": _groups(session),
            "preset_group": group_id,
            "return_to": return_to(request, "/components"),
        },
    )


@router.get("/components/{component_id:int}/edit", response_class=HTMLResponse)
def edit_component_form(
    component_id: int, request: Request, session: Session = Depends(get_session)
):
    return templates.TemplateResponse(
        request,
        "components/form.html",
        {
            "c": get_or_404(session, Component, component_id),
            "groups": _groups(session),
            "preset_group": None,
            "return_to": return_to(request, "/components"),
        },
    )


@router.get("/components/quick-new", response_class=HTMLResponse)
def quick_new_form(
    request: Request, group_id: int | None = None, session: Session = Depends(get_session)
):
    """Inline component-create dialog for the offer form (§UI §7)."""
    return templates.TemplateResponse(
        request,
        "components/_quick_new.html",
        {"groups": _groups(session), "preset_group": group_id},
    )


def _price_decimal(raw: str, *, allow_zero: bool) -> Decimal:
    """Parse a base amount/price in Hungarian notation (decimal comma accepted).
    422 on garbage, on a negative, or on a zero base_amount — matching the DB
    CHECKs (base_amount > 0, base_price >= 0)."""
    value = decimal_hu(raw)
    if value is None or value < 0 or (value == 0 and not allow_zero):
        raise HTTPException(status_code=422, detail="invalid amount/price")
    return value


@router.post("/components/quick-new", response_class=HTMLResponse)
def quick_new_create(
    request: Request,
    name: str = Form(...),
    group_id: int = Form(...),
    unit: str = Form("db"),
    type: str = Form("ingredient"),
    base_amount: str = Form(...),
    base_price: str = Form(...),
    session: Session = Depends(get_session),
):
    amount_dec = _price_decimal(base_amount, allow_zero=False)
    price_dec = _price_decimal(base_price, allow_zero=True)
    comp = Component(
        name=name.strip(), group_id=group_id, unit=unit.strip(), type=type, active=True
    )
    session.add(comp)
    session.flush()
    session.add(ComponentPrice(component_id=comp.id, base_amount=amount_dec, base_price=price_dec))
    # Commit before returning the id: the offer form immediately selects this
    # component and can submit an offer referencing it, so it must be durable
    # now — not only in get_session's post-yield teardown (cf. _helpers.see_other).
    session.commit()
    # Return a tiny script that tells the offer form to select the new component.
    return templates.TemplateResponse(
        request,
        "components/_quick_created.html",
        {"id": comp.id, "name": comp.name, "unit": comp.unit, "group_id": comp.group_id},
    )


@router.post("/components")
def create_component(
    name: str = Form(...),
    group_id: int = Form(...),
    unit: str = Form("db"),
    type: str = Form("ingredient"),
    active: bool = Form(False),
    notes: str = Form(""),
    product_id: str = Form(""),
    base_amount: str = Form(...),
    base_price: str = Form(...),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    amount_dec = _price_decimal(base_amount, allow_zero=False)
    price_dec = _price_decimal(base_price, allow_zero=True)
    comp = Component(
        name=name.strip(),
        group_id=group_id,
        unit=unit.strip(),
        type=type,
        active=active,
        notes=notes.strip() or None,
        product_id=product_id.strip() or None,
    )
    session.add(comp)
    session.flush()
    session.add(ComponentPrice(component_id=comp.id, base_amount=amount_dec, base_price=price_dec))
    return see_other_back(session, return_to, "/components")


@router.post("/components/{component_id:int}")
def update_component(
    component_id: int,
    name: str = Form(...),
    group_id: int = Form(...),
    unit: str = Form("db"),
    type: str = Form("ingredient"),
    active: bool = Form(False),
    notes: str = Form(""),
    product_id: str = Form(""),
    return_to: str = Form(""),
    session: Session = Depends(get_session),
):
    comp = get_or_404(session, Component, component_id)
    comp.name = name.strip()
    comp.group_id = group_id
    comp.unit = unit.strip()
    comp.type = type
    comp.active = active
    comp.notes = notes.strip() or None
    new_product_id = product_id.strip() or None
    # Changing/clearing the product id invalidates the old "not found" warning.
    if new_product_id != comp.product_id:
        comp.price_missing_at = None
    comp.product_id = new_product_id
    return see_other_back(session, return_to, "/components")


@router.post("/components/{component_id:int}/price")
def change_price(
    component_id: int,
    base_amount: str = Form(...),
    base_price: str = Form(...),
    session: Session = Depends(get_session),
):
    """Temporal price change (§3.4): close the open row, insert a new one sharing the instant."""
    amount_dec = _price_decimal(base_amount, allow_zero=False)
    price_dec = _price_decimal(base_price, allow_zero=True)
    now = dt.datetime.now(dt.UTC)
    open_row = session.scalars(
        select(ComponentPrice)
        .where(
            ComponentPrice.component_id == component_id,
            ComponentPrice.expiration_date.is_(None),
        )
        .order_by(ComponentPrice.effective_date.desc())
    ).first()
    if open_row is not None:
        open_row.expiration_date = now
        session.flush()  # apply expiration before inserting to satisfy the EXCLUDE constraint
    session.add(
        ComponentPrice(
            component_id=component_id,
            base_amount=amount_dec,
            base_price=price_dec,
            effective_date=now,
        )
    )
    return see_other(session, "/components")
