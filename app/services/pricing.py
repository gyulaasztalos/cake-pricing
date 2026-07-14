"""Runtime cost calculation — the Python-side mirror of v_offer_line_cost /
v_offer_cost (schema.sql / §3.0).

Cost is NEVER stored. For a given offer, each line's price uses the component
price effective on the OFFER's entry_date, with the earliest-price fallback
(§3.4). Used both for live HTMX recalc while editing and for detail/list views.

We query the DB views where an offer already exists (authoritative), and expose
a pure function for pricing a *prospective* line set on a given date (used while
building a not-yet-saved offer in the form).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ComponentPrice

ZERO = Decimal("0")


def _round_huf(value: Decimal) -> Decimal:
    """Whole-Forint rounding, matching the sheet and the SQL ROUND(...,0)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PricedLine:
    component_id: int
    amount: Decimal
    line_price: Decimal
    used_fallback_price: bool


def effective_price(
    session: Session, component_id: int, as_of: dt.datetime
) -> ComponentPrice | None:
    """The price row effective on `as_of`, else the earliest-known price (§3.4).

    Mirrors the LATERAL in v_offer_line_cost: prefer the covering window,
    otherwise fall back to the earliest effective_date. None only if the
    component has no price rows at all.
    """
    rows = session.scalars(
        select(ComponentPrice)
        .where(ComponentPrice.component_id == component_id)
        .order_by(ComponentPrice.effective_date.asc())
    ).all()
    if not rows:
        return None
    for row in rows:
        starts_ok = row.effective_date <= as_of
        ends_ok = row.expiration_date is None or as_of < row.expiration_date
        if starts_ok and ends_ok:
            return row
    return rows[0]  # earliest-price fallback


def price_line(
    session: Session, component_id: int, amount: Decimal, as_of: dt.datetime
) -> PricedLine:
    """Price one prospective line: (amount / base_amount) * base_price, rounded."""
    row = effective_price(session, component_id, as_of)
    if row is None:
        return PricedLine(component_id, amount, ZERO, used_fallback_price=False)
    covers = row.effective_date <= as_of and (
        row.expiration_date is None or as_of < row.expiration_date
    )
    raw = (amount / row.base_amount) * row.base_price
    return PricedLine(
        component_id=component_id,
        amount=amount,
        line_price=_round_huf(raw),
        used_fallback_price=not covers,
    )


def price_lines(
    session: Session,
    lines: list[tuple[int, Decimal]],
    as_of: dt.datetime,
) -> tuple[list[PricedLine], Decimal]:
    """Price a set of (component_id, amount) lines; return (priced, total)."""
    priced = [price_line(session, cid, amount, as_of) for cid, amount in lines]
    total = _round_huf(sum((p.line_price for p in priced), ZERO))
    return priced, total
