"""Offer list ordering: by deadline, nearest first, with a reverse toggle."""

from __future__ import annotations

import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def _seed() -> None:
    """Three dated offers (out of creation order) plus one with no deadline."""
    from app.db import SessionLocal
    from app.models import Customer, Offer

    s = SessionLocal()
    try:
        c = Customer(name="Sorrend Teszt")
        s.add(c)
        s.flush()
        # Insert deliberately NOT in deadline order, so a passing test cannot be
        # explained by insertion/id order.
        for theme, day in (("kozepso", 15), ("legkesobbi", 30), ("legkorabbi", 1)):
            s.add(
                Offer(
                    customer_id=c.id,
                    theme=theme,
                    status="sent",
                    due_date=dt.datetime(2026, 9, day, tzinfo=dt.UTC),
                )
            )
        s.add(Offer(customer_id=c.id, theme="hataridotlen", status="sent", due_date=None))
        s.commit()
    finally:
        s.close()


def _themes(**params) -> list[str]:
    from app.db import SessionLocal
    from app.routers.offers import list_offers

    s = SessionLocal()
    try:
        resp = list_offers(request=_FakeRequest(), session=s, **params)
        return [o.theme for o in resp.context["offers"]]
    finally:
        s.close()


class _FakeRequest:
    headers: dict[str, str] = {}
    scope: dict = {"type": "http", "app": app, "router": app.router}
    url = None


def test_default_order_is_nearest_deadline_first(clean_db):
    _seed()
    assert _themes()[:3] == ["legkorabbi", "kozepso", "legkesobbi"]


def test_desc_toggle_reverses_to_latest_first(clean_db):
    _seed()
    assert _themes(desc=True)[:3] == ["legkesobbi", "kozepso", "legkorabbi"]


@pytest.mark.parametrize("desc", [False, True])
def test_undated_offers_always_sort_last(clean_db, desc):
    """An offer with no deadline is never urgent — it must not head either view."""
    _seed()
    assert _themes(desc=desc)[-1] == "hataridotlen"


def test_toggle_renders_and_round_trips_its_state(clean_db):
    _seed()
    off = client.get("/offers").text
    assert 'name="desc" value="true"' in off  # the toggle is present
    assert 'name="desc" value="true" checked' not in off  # unchecked by default
    on = client.get("/offers", params={"f": "1", "desc": "true"}).text
    assert 'name="desc" value="true" checked' in on  # stays ticked after a filter


def test_ordering_survives_the_other_filters(clean_db):
    """Sorting must not disturb the status filter (or vice versa)."""
    _seed()
    themes = _themes(f="1", status=["sent"], desc=True)
    assert themes[:3] == ["legkesobbi", "kozepso", "legkorabbi"]
