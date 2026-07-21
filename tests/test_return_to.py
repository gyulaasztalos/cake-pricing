"""Form return-to: safe-path guard, and the offer new/save round-trip."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers._helpers import _safe_path

client = TestClient(app)


# --- open-redirect guard (pure) ----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/naptar?ym=2026-07", "/naptar?ym=2026-07"),  # relative path + query kept
        ("/offers", "/offers"),
        ("//evil.com", None),  # protocol-relative
        ("https://evil.com/x", None),  # absolute foreign host
        ("javascript:alert(1)", None),  # non-http scheme
        ("/a\\b", None),  # backslash
        ("offers", None),  # not rooted at /
        ("", None),
        (None, None),
    ],
)
def test_safe_path_relative_only(raw, expected):
    assert _safe_path(raw, same_host=None) == expected


def test_safe_path_allows_same_host_absolute():
    assert (
        _safe_path("https://torta.local/naptar?ym=2026-07", "torta.local") == "/naptar?ym=2026-07"
    )
    assert _safe_path("https://torta.local/x", "other.host") is None


# --- DB-backed round-trip ----------------------------------------------------

pytestmark_db = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytestmark_db
def test_new_offer_form_prefills_due_date_and_return(clean_db):
    r = client.get(
        "/offers/new?due_date=2026-07-13",
        headers={"referer": "http://testserver/naptar?ym=2026-07"},
    )
    assert r.status_code == 200
    assert 'name="due_date" value="2026-07-13"' in r.text
    # Referer became the return target (hidden field + cancel link).
    assert 'name="return_to" value="/naptar?ym=2026-07"' in r.text


@pytestmark_db
def test_explicit_next_wins_and_works_without_referer(clean_db):
    # The calendar link passes ?next= explicitly because the edge strips the
    # Referer — so this must work with NO referer header at all.
    r = client.get("/offers/new?due_date=2026-07-13&next=/naptar?ym=2026-07")
    assert r.status_code == 200
    assert 'name="return_to" value="/naptar?ym=2026-07"' in r.text
    # And the Cancel link points there too.
    assert 'href="/naptar?ym=2026-07"' in r.text


@pytestmark_db
def test_calendar_day_links_carry_next(clean_db):
    # Regression for "cancel from a calendar-opened offer lands on /offers":
    # every day cell links to a pre-dated new offer AND carries an explicit next=
    # back to the month (the edge strips the Referer, so we can't rely on it).
    r = client.get("/naptar?ym=2026-07")
    assert r.status_code == 200
    assert "/offers/new?due_date=2026-07-01&next=" in r.text


@pytestmark_db
def test_new_offer_form_ignores_bad_due_date(clean_db):
    r = client.get("/offers/new?due_date=not-a-date")
    assert r.status_code == 200
    assert 'name="due_date" value=""' in r.text


@pytestmark_db
def test_create_offer_redirects_to_return_to(clean_db):
    from app.db import SessionLocal
    from app.models import Customer

    s = SessionLocal()
    try:
        cust = Customer(name="Teszt")
        s.add(cust)
        s.commit()
        cid = cust.id
    finally:
        s.rollback()
        s.close()

    good = client.post(
        "/offers",
        data={"customer_id": cid, "status": "draft", "return_to": "/naptar?ym=2026-07"},
        follow_redirects=False,
    )
    assert good.status_code == 303
    assert good.headers["location"] == "/naptar?ym=2026-07"

    # A hostile return_to falls back to the list instead of redirecting off-site.
    evil = client.post(
        "/offers",
        data={"customer_id": cid, "status": "draft", "return_to": "//evil.com"},
        follow_redirects=False,
    )
    assert evil.status_code == 303
    assert evil.headers["location"] == "/offers"
