"""Készlet (Inventory) — stock-item list with on-hand, deliveries & corrections (§3.3).

Order consumption is automatic (negative movements posted when offers save).
Here the chef records supplier deliveries (+qty) and corrections (±qty). Warning
only at zero — never blocks.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import Component, StockMovement
from app.services import stock
from app.templating import templates

router = APIRouter()


@router.get("/inventory", response_class=HTMLResponse)
def list_inventory(
    request: Request,
    q: str = "",
    only_low: bool = False,
    session: Session = Depends(get_session),
):
    items = list(
        session.scalars(
            select(Component)
            .where(Component.type == "stock_item")
            .options(selectinload(Component.group))
            .order_by(Component.name)
        )
    )
    rows = []
    for c in items:
        if q and q.lower() not in c.name.lower():
            continue
        on_hand = stock.on_hand(session, c.id)
        if only_low and on_hand > 0:
            continue
        rows.append({"c": c, "on_hand": on_hand})
    ctx = {"rows": rows, "q": q, "only_low": only_low, "active_nav": "inventory"}
    tmpl = "inventory/_rows.html" if request.headers.get("HX-Request") else "inventory/list.html"
    return templates.TemplateResponse(request, tmpl, ctx)


@router.get("/inventory/detail/{component_id}", response_class=HTMLResponse)
def inventory_detail(component_id: int, request: Request, session: Session = Depends(get_session)):
    movements = list(
        session.scalars(
            select(StockMovement)
            .where(StockMovement.component_id == component_id)
            .order_by(StockMovement.entry_date.desc())
        )
    )
    return templates.TemplateResponse(
        request,
        "inventory/_detail.html",
        {"movements": movements, "on_hand": stock.on_hand(session, component_id)},
    )


@router.get("/inventory/receive", response_class=HTMLResponse)
def receive_form(request: Request, session: Session = Depends(get_session)):
    items = list(
        session.scalars(
            select(Component).where(Component.type == "stock_item").order_by(Component.name)
        )
    )
    return templates.TemplateResponse(request, "inventory/receive.html", {"items": items})


@router.post("/inventory/receive")
def receive(
    component_id: int = Form(...),
    reason: str = Form("delivery"),
    qty: str = Form(...),
    session: Session = Depends(get_session),
):
    try:
        amount = Decimal(qty)
    except InvalidOperation:
        return RedirectResponse(url="/inventory", status_code=303)
    if reason == "correction":
        stock.record_correction(session, component_id, amount)
    else:
        stock.record_delivery(session, component_id, amount)
    return RedirectResponse(url="/inventory", status_code=303)
