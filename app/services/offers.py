"""Offer persistence helpers: build the grouped view-model for the form, and
save (create/edit) an offer's lines, keeping stock consumption in sync (§3.7).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Component, Group, Offer, OfferComponent
from app.services import stock
from app.services.pricing import price_from_rows, prices_for


@dataclass
class LineVM:
    component_id: int
    name: str
    unit: str
    amount: Decimal
    line_price: Decimal
    used_fallback: bool
    is_stock: bool
    on_hand: Decimal | None


@dataclass
class GroupVM:
    group: Group
    lines: list[LineVM] = field(default_factory=list)
    subtotal: Decimal = Decimal("0")


def build_group_vms(
    session: Session,
    line_pairs: list[tuple[int, Decimal]],
    as_of: dt.datetime,
) -> tuple[list[GroupVM], Decimal]:
    """Group (component_id, amount) pairs into per-group view-models with costs.

    Every group is represented (even empty ones) so the form shows all sections
    (empty groups visible — UI decision). Lines keep input order within a group.
    """
    groups = list(session.scalars(select(Group).order_by(Group.name)))
    vms = {g.id: GroupVM(group=g) for g in groups}
    total = Decimal("0")

    comp_ids = [cid for cid, _ in line_pairs]
    if not comp_ids:
        return [vms[g.id] for g in groups], total

    # Batch-load everything referenced (3 queries total, not N+1):
    comps = {
        c.id: c for c in session.scalars(select(Component).where(Component.id.in_(comp_ids)))
    }
    price_rows = prices_for(session, comp_ids)
    stock_ids = [cid for cid in comp_ids if (c := comps.get(cid)) and c.type == "stock_item"]
    on_hand = stock.on_hand_for(session, stock_ids)

    for cid, amount in line_pairs:
        comp = comps.get(cid)
        if comp is None:
            continue
        priced = price_from_rows(price_rows.get(cid, []), amount, as_of, cid)
        is_stock = comp.type == "stock_item"
        vm = vms[comp.group_id]
        vm.lines.append(
            LineVM(
                component_id=cid,
                name=comp.name,
                unit=comp.unit,
                amount=amount,
                line_price=priced.line_price,
                used_fallback=priced.used_fallback_price,
                is_stock=is_stock,
                on_hand=on_hand.get(cid) if is_stock else None,
            )
        )
        vm.subtotal += priced.line_price
        total += priced.line_price
    return [vms[g.id] for g in groups], total


def save_offer_lines(session: Session, offer: Offer, line_pairs: list[tuple[int, Decimal]]) -> None:
    """Replace an offer's lines with `line_pairs`, then resync stock consumption.

    Called on both create and edit. entry_date (pricing date) is NOT touched here
    — it stays immutable (§3.4).
    """
    offer.components.clear()
    session.flush()
    for cid, amount in line_pairs:
        session.add(OfferComponent(offer_id=offer.id, component_id=cid, amount=amount))
    session.flush()
    stock.sync_offer_consumption(session, offer.id)


def load_offer_line_pairs(session: Session, offer_id: int) -> list[tuple[int, Decimal]]:
    rows = session.scalars(
        select(OfferComponent)
        .where(OfferComponent.offer_id == offer_id)
        .options(selectinload(OfferComponent.component))
        .order_by(OfferComponent.id)
    )
    return [(r.component_id, r.amount) for r in rows]
