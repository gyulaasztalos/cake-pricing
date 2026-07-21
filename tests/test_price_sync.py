"""Price-sync: XLSX parsing (avg dup prices) + reconciliation against the DB."""

from __future__ import annotations

import os
from decimal import Decimal
from io import BytesIO

import openpyxl
import pytest

from app.services import price_sync


def _xlsx(rows: list[list[object]]) -> bytes:
    """Build a minimal árfigyelö-shaped workbook: header row + given data rows
    (each row must have >= 9 cells; col A = id, col I = price)."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Termék azonosító"] + [f"col{c}" for c in "BCDEFGH"] + ["Maximum ár"])
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --- parsing (pure) ----------------------------------------------------------


def test_parse_prices_averages_duplicates_and_parses_comma():
    data = _xlsx(
        [
            ["0000000022989", *["x"] * 7, "499,0000"],  # single, digit id
            ["aldi-10026", *["x"] * 7, "1299,0000"],  # text id (chain-prefixed)
            ["0000040278063", *["x"] * 7, "769,0000"],  # dup 1
            ["0000040278063", *["x"] * 7, "388,0000"],  # dup 2 -> avg 578.5 -> 579
            ["", *["x"] * 7, "100"],  # blank id skipped
            ["0000000099999", *["x"] * 7, "not-a-price"],  # bad price skipped
        ]
    )
    prices = price_sync.parse_prices(data)
    assert prices["0000000022989"] == Decimal("499")
    assert prices["aldi-10026"] == Decimal("1299")  # arbitrary text id works
    assert prices["0000040278063"] == Decimal("579")  # (769+388)/2 rounded
    assert "" not in prices
    assert "0000000099999" not in prices


# --- reconciliation (DB) -----------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture
def session():
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _component(session, name, product_id, price):
    from app.models import Component, ComponentPrice, Group

    gid = session.query(Group).filter_by(name="Piskóta").one().id
    c = Component(name=name, group_id=gid, unit="g", type="ingredient", product_id=product_id)
    session.add(c)
    session.flush()
    if price is not None:
        session.add(
            ComponentPrice(component_id=c.id, base_amount=Decimal("1"), base_price=Decimal(price))
        )
    session.commit()
    return c


@pytestmark_db
def test_run_sync_updates_missing_and_unchanged(clean_db, session):
    from app.models import ComponentPrice
    from app.services.pricing import effective_price

    changed = _component(session, "Cukor", "0000000022989", "400")  # price will change 400 -> 499
    same = _component(session, "Liszt", "0000000023023", "1539")  # already 1539 -> no change
    gone = _component(session, "Vaj", "0000000099999", "800")  # not in feed -> missing
    no_id = _component(session, "Só", None, "10")  # no product_id -> ignored

    prices = {"0000000022989": Decimal("499"), "0000000023023": Decimal("1539")}
    result = price_sync.run_sync(session, prices)
    session.commit()

    assert result.checked == 3  # changed + same + gone (no_id excluded)
    assert [(c.name, c.old_price, c.new_price) for c in result.changes] == [
        ("Cukor", Decimal("400"), Decimal("499"))
    ]
    assert result.missing == ["Vaj"]

    # The changed component now prices at 499, via a NEW temporal row (old closed).
    # Use a safely-future as_of: the DB container's clock can run milliseconds
    # ahead of this process, so a bare now() may predate the just-written window.
    import datetime as dt

    as_of = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)
    assert effective_price(session, changed.id, as_of).base_price == Decimal("499")
    rows = (
        session.query(ComponentPrice)
        .filter_by(component_id=changed.id)
        .order_by(ComponentPrice.effective_date)
        .all()
    )
    assert len(rows) == 2  # append-only: original + new
    assert rows[0].expiration_date is not None  # old window closed

    # Unchanged component keeps a single price row; not-found got the warning.
    assert session.query(ComponentPrice).filter_by(component_id=same.id).count() == 1
    session.refresh(gone)
    session.refresh(changed)
    assert gone.price_missing_at is not None
    assert changed.price_missing_at is None
    assert no_id.price_missing_at is None
