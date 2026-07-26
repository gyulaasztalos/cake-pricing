"""Slice count (Szelet): form round-trip, the Ft/szelet note, and the stats."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.offers import _parse_portions

client = TestClient(app)


# --- parsing (pure) ----------------------------------------------------------


@pytest.mark.parametrize(("raw", "expected"), [("12", 12), (" 8 ", 8), ("500", 500), ("1", 1)])
def test_parse_portions_accepts_valid(raw, expected):
    assert _parse_portions(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "-3", "501", "2.5"])
def test_parse_portions_rejects_invalid_as_none(raw):
    assert _parse_portions(raw) is None


# --- DB-backed ---------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def _customer() -> int:
    from app.db import SessionLocal
    from app.models import Customer

    s = SessionLocal()
    try:
        c = Customer(name="Szeletes Teszt")
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def _offer(cid: int, *, portions=None, final=None, theme="Torta", due=None) -> int:
    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        o = Offer(
            customer_id=cid,
            theme=theme,
            status="accepted",
            portions=portions,
            final_price=Decimal(final) if final is not None else None,
            due_date=due,
        )
        s.add(o)
        s.commit()
        return o.id
    finally:
        s.close()


@pytestmark_db
def test_offer_form_saves_portions(clean_db):
    cid = _customer()
    r = client.post(
        "/offers",
        data={"customer_id": str(cid), "status": "draft", "portions": "12"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        assert s.query(Offer).order_by(Offer.id.desc()).first().portions == 12
    finally:
        s.close()


@pytestmark_db
def test_list_and_calendar_show_portions_label(clean_db):
    # A deadline puts the offer on the calendar month view as well as the list.
    _offer(
        _customer(),
        portions=12,
        final="15000",
        due=dt.datetime(2026, 7, 15, tzinfo=dt.UTC),
    )
    assert "12 szeletes" in client.get("/offers", params={"f": "1"}).text
    assert "12 szeletes" in client.get("/schedule", params={"ym": "2026-07"}).text


@pytestmark_db
def test_no_portions_shows_no_label(clean_db):
    _offer(_customer(), portions=None, final="15000")
    assert "szeletes" not in client.get("/offers", params={"f": "1"}).text


@pytestmark_db
def test_per_portion_note_uses_final_price_not_paid(clean_db):
    from app.db import SessionLocal
    from app.models import Offer

    cid = _customer()
    oid = _offer(cid, portions=12, final="15000")
    # A different paid amount must NOT change the note (final price is the basis).
    s = SessionLocal()
    try:
        s.get(Offer, oid).paid = Decimal("6000")
        s.commit()
    finally:
        s.close()

    # format_huf uses a NON-BREAKING space as the thousands separator.
    expected = "1\xa0250\xa0Ft / szelet"
    assert expected in client.get(f"/offers/detail/{oid}").text  # 15000/12, whole Ft
    form = client.get(f"/offers/{oid}/edit").text
    assert expected in form
    assert 'name="portions"' in form and 'value="12"' in form


@pytestmark_db
def test_per_portion_note_absent_without_both_values(clean_db):
    cid = _customer()
    no_price = _offer(cid, portions=12, final=None)
    no_portions = _offer(cid, portions=None, final="15000")
    assert "/ szelet" not in client.get(f"/offers/detail/{no_price}").text
    assert "/ szelet" not in client.get(f"/offers/detail/{no_portions}").text


@pytestmark_db
def test_copy_carries_portions(clean_db):
    oid = _offer(_customer(), portions=16, final="20000")
    form = client.get(f"/offers/{oid}/copy").text
    assert 'name="portions"' in form and 'value="16"' in form


@pytestmark_db
def test_stats_group_by_portions_and_overall_average(clean_db):
    from app.db import SessionLocal
    from app.services import stats as stats_svc

    cid = _customer()
    _offer(cid, portions=12, final="12000")  # 1000/slice
    _offer(cid, portions=12, final="18000")  # 1500/slice → avg 1250 for 12
    _offer(cid, portions=24, final="24000")  # 1000/slice
    _offer(cid, portions=None, final="9000")  # excluded (no slice count)
    _offer(cid, portions=8, final=None)  # excluded (no price)

    s = SessionLocal()
    try:
        st = stats_svc.collect(s, None)
    finally:
        s.close()

    by = {p.portions: p for p in st.by_portions}
    assert by[12].offers == 2
    assert by[12].avg_per_portion == Decimal("1250")
    assert by[24].offers == 1
    assert by[24].avg_per_portion == Decimal("1000")
    assert 8 not in by  # unpriced offer contributes nothing
    # Overall = mean of per-offer slice prices: (1000 + 1500 + 1000) / 3.
    assert st.avg_per_portion is not None
    assert round(st.avg_per_portion) == round(Decimal("3500") / 3)
