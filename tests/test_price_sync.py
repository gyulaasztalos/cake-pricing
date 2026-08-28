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


def test_parse_prices_medians_duplicates_and_parses_comma():
    data = _xlsx(
        [
            ["0000000022989", *["x"] * 7, "499,0000"],  # single, digit id
            ["aldi-10026", *["x"] * 7, "1299,0000"],  # text id (chain-prefixed)
            ["0000040278063", *["x"] * 7, "769,0000"],  # dup 1
            ["0000040278063", *["x"] * 7, "388,0000"],  # dup 2 -> midpoint 578.5 -> 579
            ["", *["x"] * 7, "100"],  # blank id skipped
            ["0000000099999", *["x"] * 7, "not-a-price"],  # bad price skipped
        ]
    )
    prices = price_sync.parse_prices(data)
    assert prices["0000000022989"].value == Decimal("499")
    assert prices["aldi-10026"].value == Decimal("1299")  # arbitrary text id works
    assert prices["0000040278063"].value == Decimal("579")  # (769+388)/2 rounded
    assert "" not in prices
    assert "0000000099999" not in prices


def test_one_nonsense_chain_row_cannot_drag_the_price():
    """The real regression: 2026-08-28, Koronás Cukor kristálycukor 1000 g.

    Auchan 318, Aldi 2990, Tesco 331 — the mean is 1213 Ft, a price no shop
    charged, and that is what got written to the component. The median ignores the
    bad row. (Aldi's own unit price on that row said 5980 Ft/kg for a 1 kg bag, so
    the row was internally inconsistent; the publisher had pulled it within hours.)
    """
    data = _xlsx(
        [
            ["5998501325027", *["x"] * 7, "318,0000"],  # Auchan
            ["5998501325027", *["x"] * 7, "2990,0000"],  # Aldi — nonsense
            ["5998501325027", *["x"] * 7, "331,0000"],  # Tesco
        ]
    )
    feed = price_sync.parse_prices(data)["5998501325027"]
    assert feed.value == Decimal("331")  # NOT 1213
    assert feed.reliable is True  # three rows: the median outvotes the odd one


def test_two_contradictory_rows_are_not_trusted():
    """With only two rows the median sits between them, so it cannot outvote a bad
    one — 106 products in one feed sample looked like this. Better to keep the last
    known-good price and say so than to publish the midpoint of a contradiction."""
    data = _xlsx(
        [
            ["0000042439547", *["x"] * 7, "499,0000"],
            ["0000042439547", *["x"] * 7, "1889,0000"],
        ]
    )
    feed = price_sync.parse_prices(data)["0000042439547"]
    assert feed.reliable is False
    assert (feed.low, feed.high) == (Decimal("499.0000"), Decimal("1889.0000"))


def test_a_wide_spread_over_three_rows_is_still_usable():
    """Only the 2-row case is unarbitrable; 3+ rows keep working."""
    data = _xlsx(
        [
            ["x1", *["x"] * 7, "100,0000"],
            ["x1", *["x"] * 7, "105,0000"],
            ["x1", *["x"] * 7, "900,0000"],
        ]
    )
    feed = price_sync.parse_prices(data)["x1"]
    assert feed.reliable is True
    assert feed.value == Decimal("105")


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

    def _feed(v: str) -> price_sync.FeedPrice:
        return price_sync.FeedPrice(
            value=Decimal(v), samples=1, low=Decimal(v), high=Decimal(v), reliable=True
        )

    prices = {"0000000022989": _feed("499"), "0000000023023": _feed("1539")}
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


def test_an_untrusted_feed_keeps_the_last_good_price(clean_db, session):
    """A contradictory feed must not overwrite a known-good price — and must not
    be silently dropped either, or the chef never learns the source is broken."""
    from app.models import ComponentPrice

    comp = _component(session, "Cukor", "5998501325027", "331")
    prices = {
        "5998501325027": price_sync.FeedPrice(
            value=Decimal("1194"),  # the midpoint of a contradiction
            samples=2,
            low=Decimal("499"),
            high=Decimal("1889"),
            reliable=False,
        )
    }
    result = price_sync.run_sync(session, prices)
    session.commit()

    assert result.changes == []  # price untouched
    assert session.query(ComponentPrice).filter_by(component_id=comp.id).count() == 1
    assert [(u.name, u.low, u.high) for u in result.unreliable] == [
        ("Cukor", Decimal("499"), Decimal("1889"))
    ]
    session.refresh(comp)
    assert comp.price_missing_at is None  # it WAS found; it is just not trustworthy
