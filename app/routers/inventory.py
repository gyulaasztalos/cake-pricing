"""Készlet (Inventory) — stock-item list with on-hand, deliveries & corrections (§3.3).

Order consumption is automatic (negative movements posted when offers save).
Here the chef records supplier deliveries (+qty) and corrections (±qty). Warning
only at zero — never blocks.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import Component, StockMovement
from app.routers._helpers import see_other
from app.services import stock
from app.services.pricing import price_from_rows, prices_for
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
    now = dt.datetime.now(dt.UTC)
    # One query for every stock item's price history (no N+1), then value each row
    # with the same (amount / base_amount) * base_price rounding offers use.
    price_rows = prices_for(session, [c.id for c in items])
    rows = []
    total_value = Decimal(0)
    for c in items:
        if q and q.lower() not in c.name.lower():
            continue
        on_hand = stock.on_hand(session, c.id)
        if only_low and on_hand > 0:
            continue
        # Nothing on the shelf is worth nothing: a zero/negative balance (an
        # over-consumed item) contributes 0 rather than a negative value.
        value = (
            price_from_rows(price_rows.get(c.id, []), on_hand, now, c.id).line_price
            if on_hand > 0
            else Decimal(0)
        )
        total_value += value
        rows.append({"c": c, "on_hand": on_hand, "value": value})
    # The total covers the rows actually shown, so it stays consistent with an
    # active search / "only low" filter.
    ctx = {
        "rows": rows,
        "q": q,
        "only_low": only_low,
        "total_value": total_value,
        "active_nav": "inventory",
    }
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
        return see_other(session, "/inventory")
    if reason == "correction":
        stock.record_correction(session, component_id, amount)
    else:
        stock.record_delivery(session, component_id, amount)
    return see_other(session, "/inventory")
