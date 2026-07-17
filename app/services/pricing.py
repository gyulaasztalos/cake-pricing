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
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ComponentPrice

ZERO = Decimal("0")


def _round_huf(value: Decimal) -> Decimal:
    """Whole-Forint rounding, matching the sheet and the SQL ROUND(...,0)."""
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def pick_effective(rows: list[ComponentPrice], as_of: dt.datetime) -> ComponentPrice | None:
    """From a component's price rows (ascending by effective_date), pick the one
    effective on `as_of`, else fall back to the earliest (§3.4)."""
    if not rows:
        return None
    for row in rows:
        if row.effective_date <= as_of and (
            row.expiration_date is None or as_of < row.expiration_date
        ):
            return row
    return rows[0]


@dataclass(frozen=True)
class PricedLine:
    component_id: int
    amount: Decimal
    line_price: Decimal
    used_fallback_price: bool


def prices_for(
    session: Session, component_ids: Iterable[int]
) -> dict[int, list[ComponentPrice]]:
    """Fetch all price rows for the given components in ONE query, grouped by
    component_id and sorted ascending by effective_date. Avoids the N+1 that a
    per-component lookup would cause on large offers / the components list."""
    ids = list(dict.fromkeys(component_ids))  # de-dupe, keep order-independence
    if not ids:
        return {}
    grouped: dict[int, list[ComponentPrice]] = {i: [] for i in ids}
    for row in session.scalars(
        select(ComponentPrice)
        .where(ComponentPrice.component_id.in_(ids))
        .order_by(ComponentPrice.component_id, ComponentPrice.effective_date.asc())
    ):
        grouped[row.component_id].append(row)
    return grouped


def effective_price(
    session: Session, component_id: int, as_of: dt.datetime
) -> ComponentPrice | None:
    """The price row effective on `as_of`, else the earliest-known price (§3.4)."""
    return pick_effective(prices_for(session, [component_id]).get(component_id, []), as_of)


def price_from_rows(
    rows: list[ComponentPrice], amount: Decimal, as_of: dt.datetime, component_id: int
) -> PricedLine:
    """Price one line from already-fetched price rows (no DB access)."""
    row = pick_effective(rows, as_of)
    if row is None:
        return PricedLine(component_id, amount, ZERO, used_fallback_price=False)
    covers = row.effective_date <= as_of and (
        row.expiration_date is None or as_of < row.expiration_date
    )
    raw = (amount / row.base_amount) * row.base_price
    return PricedLine(component_id, amount, _round_huf(raw), used_fallback_price=not covers)


def price_line(
    session: Session, component_id: int, amount: Decimal, as_of: dt.datetime
) -> PricedLine:
    """Price one prospective line: (amount / base_amount) * base_price, rounded."""
    rows = prices_for(session, [component_id]).get(component_id, [])
    return price_from_rows(rows, amount, as_of, component_id)
