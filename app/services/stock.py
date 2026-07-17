"""Inventory ledger helpers (§3.3).

Current stock = SUM(qty_delta) per component (mirrors v_component_stock).
Order consumption is a negative movement carrying the offer_id; deliveries and
corrections carry no offer. When an offer's stock-item lines change (edit) or the
offer is deleted, movements tied to that offer are reversed — for delete the DB
FK cascade removes them; for edit we resync explicitly (§3.7).
Warning-only at zero — never blocks (enforced here / in the UI, not the DB).
"""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import Component, OfferComponent, StockMovement

ZERO = Decimal("0")


def on_hand(session: Session, component_id: int) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(StockMovement.qty_delta), 0)).where(
            StockMovement.component_id == component_id
        )
    )
    return Decimal(total or 0)


def on_hand_for(session: Session, component_ids: Iterable[int]) -> dict[int, Decimal]:
    """SUM(qty_delta) per component for many components in ONE query (avoids N+1)."""
    ids = list(dict.fromkeys(component_ids))
    if not ids:
        return {}
    rows = session.execute(
        select(StockMovement.component_id, func.coalesce(func.sum(StockMovement.qty_delta), 0))
        .where(StockMovement.component_id.in_(ids))
        .group_by(StockMovement.component_id)
    ).all()
    result = dict.fromkeys(ids, ZERO)
    for cid, total in rows:
        result[cid] = Decimal(total or 0)
    return result


def record_delivery(session: Session, component_id: int, qty: Decimal) -> StockMovement:
    mv = StockMovement(component_id=component_id, qty_delta=qty, reason="delivery")
    session.add(mv)
    return mv


def record_correction(session: Session, component_id: int, delta: Decimal) -> StockMovement:
    mv = StockMovement(component_id=component_id, qty_delta=delta, reason="correction")
    session.add(mv)
    return mv


def sync_offer_consumption(session: Session, offer_id: int) -> None:
    """Rebuild the 'order' movements for an offer from its current stock-item lines.

    Idempotent: deletes this offer's existing order-movements and re-creates one
    negative movement per stock-item line. Called after creating/editing an offer
    so consumption always matches the saved lines (§3.7).
    """
    session.execute(
        delete(StockMovement).where(
            StockMovement.offer_id == offer_id, StockMovement.reason == "order"
        )
    )
    session.flush()
    rows = session.execute(
        select(OfferComponent.component_id, OfferComponent.amount)
        .join(Component, Component.id == OfferComponent.component_id)
        .where(OfferComponent.offer_id == offer_id, Component.type == "stock_item")
    ).all()
    for component_id, amount in rows:
        session.add(
            StockMovement(
                component_id=component_id,
                qty_delta=-amount,
                reason="order",
                offer_id=offer_id,
            )
        )
