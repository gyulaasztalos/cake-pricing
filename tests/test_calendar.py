"""Naptár: month view, .ics feed content, and the feed's token gate."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services import calendar as calendar_svc

client = TestClient(app)

BKK = dt.timezone(dt.timedelta(hours=2))


# --- pure helpers (no DB) -----------------------------------------------------


def test_shift_month_rolls_the_year():
    assert calendar_svc.shift_month(2026, 1, -1) == (2025, 12)
    assert calendar_svc.shift_month(2026, 12, 1) == (2027, 1)
    assert calendar_svc.shift_month(2026, 7, 1) == (2026, 8)


def test_escape_follows_rfc5545():
    out = calendar_svc._escape("a,b;c\\d\ne")
    assert out == "a\\,b\\;c\\\\d\\ne"


def test_fold_counts_octets_not_characters():
    # 60 Hungarian accented chars = 120 octets -> must fold, and each physical
    # line must stay within 75 octets.
    line = "SUMMARY:" + ("á" * 60)
    folded = calendar_svc._fold(line)
    assert "\r\n " in folded
    for physical in folded.split("\r\n"):
        assert len(physical.encode("utf-8")) <= 75
    # Unfolding restores the original content.
    assert folded.replace("\r\n ", "") == line


def test_feed_disabled_without_token():
    # settings.calendar_token is empty in tests -> feed must 404, not 401,
    # so the path never confirms it exists.
    assert settings.calendar_token == ""
    assert client.get("/calendar/anything/offers.ics").status_code == 404


def test_feed_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr(settings, "calendar_token", "s3cret")
    assert client.get("/calendar/wrong/offers.ics").status_code == 404


# --- DB-backed ----------------------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture
def session():
    """A Session that is ALWAYS closed.

    Never call SessionLocal() inline in a test: an unclosed session stays
    'idle in transaction' holding row locks, and the next test's clean_db
    TRUNCATE then blocks on AccessExclusiveLock forever (the suite just hangs).
    """
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


def _make_offer(session, customer_id, *, status, due, final, theme=None, flavor=None):
    from app.models import Offer

    o = Offer(
        customer_id=customer_id,
        status=status,
        due_date=due,
        final_price=Decimal(final) if final is not None else None,
        theme=theme,
        flavor=flavor,
    )
    session.add(o)
    session.commit()
    return o.id


@pytestmark_db
def test_month_days_buckets_by_local_date_and_excludes_rejected(clean_db, session):
    from app.models import Customer

    cust = Customer(name="Teszt Ügyfél")
    session.add(cust)
    session.commit()

    # 2026-07-13 23:30 Budapest -> 21:30 UTC; must land on the 13th, not the 14th.
    _make_offer(
        session,
        cust.id,
        status="accepted",
        due=dt.datetime(2026, 7, 13, 23, 30, tzinfo=BKK),
        final="45000",
        theme="Unikornis",
        flavor="Csoki",
    )
    # Rejected offers never appear.
    _make_offer(
        session,
        cust.id,
        status="rejected",
        due=dt.datetime(2026, 7, 15, 12, 0, tzinfo=BKK),
        final="9000",
    )

    days = calendar_svc.month_days(session, 2026, 7)
    assert len(days) == 31  # every day of the month is rendered
    by_date = {d.date.day: d for d in days}
    assert len(by_date[13].offers) == 1
    assert by_date[14].offers == []
    assert by_date[15].offers == []  # the rejected one is gone

    # Weekend / week-start flags: 2026-07-13 is a Monday, 18-19 a weekend.
    assert by_date[13].starts_week is True
    assert by_date[18].is_weekend and by_date[19].is_weekend
    assert by_date[13].is_weekend is False


@pytestmark_db
def test_ics_feed_contains_all_required_fields(clean_db, session, monkeypatch):
    from app.models import Customer

    cust = Customer(name="Kiss Anna")
    session.add(cust)
    session.commit()
    _make_offer(
        session,
        cust.id,
        status="accepted",
        due=dt.datetime(2026, 7, 13, 10, 0, tzinfo=BKK),
        final="45000",
        theme="Unikornis",
        flavor="Csoki",
    )
    # Release this session's locks before the request opens its own session.
    session.rollback()

    monkeypatch.setattr(settings, "calendar_token", "s3cret")
    r = client.get("/calendar/s3cret/offers.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert body.startswith("BEGIN:VCALENDAR\r\n")
    assert body.rstrip().endswith("END:VCALENDAR")
    # All-day event on the local date.
    assert "DTSTART;VALUE=DATE:20260713" in body
    assert "DTEND;VALUE=DATE:20260714" in body
    # Every required field is present (unfold first: long lines are wrapped).
    unfolded = body.replace("\r\n ", "")
    assert "Kiss Anna" in unfolded
    assert "Unikornis" in unfolded
    assert "Csoki" in unfolded
    assert "Elfogadva" in unfolded  # status label
    assert "45 000 Ft" in unfolded  # final price as quoted
    assert "UID:offer-" in unfolded


@pytestmark_db
def test_month_page_renders(clean_db):
    r = client.get("/schedule?ym=2026-07")
    assert r.status_code == 200
    assert "2026. július" in r.text
    # Bad input falls back to the current month rather than erroring.
    assert client.get("/schedule?ym=nonsense").status_code == 200
