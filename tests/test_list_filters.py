"""Multi-select list filters: offers status (default subset) + components group."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def _seed_offers(statuses: list[str]) -> None:
    from app.db import SessionLocal
    from app.models import Customer, Offer

    s = SessionLocal()
    try:
        cust = Customer(name="Szűrő Teszt")
        s.add(cust)
        s.flush()
        for st in statuses:
            s.add(Offer(customer_id=cust.id, theme=f"T-{st}", status=st))
        s.commit()
    finally:
        s.close()


ALL = ["draft", "sent", "accepted", "rejected", "done"]


def test_offers_default_hides_rejected_and_done(clean_db):
    _seed_offers(ALL)
    r = client.get("/offers")  # first load, no filter submitted
    assert r.status_code == 200
    for st in ("draft", "sent", "accepted"):
        assert f"T-{st}" in r.text
    for st in ("rejected", "done"):
        assert f"T-{st}" not in r.text


def test_offers_explicit_status_selection_wins(clean_db):
    _seed_offers(ALL)
    # f=1 marks an explicit submission; only rejected requested.
    r = client.get("/offers", params={"f": "1", "status": ["rejected"]})
    assert "T-rejected" in r.text
    for st in ("draft", "sent", "accepted", "done"):
        assert f"T-{st}" not in r.text


def test_offers_explicit_empty_shows_all(clean_db):
    _seed_offers(ALL)
    # Marker present but nothing checked -> no status filter -> everything shows.
    r = client.get("/offers", params={"f": "1"})
    for st in ALL:
        assert f"T-{st}" in r.text


def test_offers_multiple_statuses(clean_db):
    _seed_offers(ALL)
    r = client.get("/offers", params={"f": "1", "status": ["draft", "done"]})
    assert "T-draft" in r.text and "T-done" in r.text
    for st in ("sent", "accepted", "rejected"):
        assert f"T-{st}" not in r.text


def test_components_group_multiselect(clean_db, seed_component):
    seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    seed_component("Vaj", "Töltelék", "g", "ingredient", "1000", "500")

    from app.db import SessionLocal
    from app.models import Group

    s = SessionLocal()
    try:
        piskota = s.query(Group).filter_by(name="Piskóta").one().id
        toltelek = s.query(Group).filter_by(name="Töltelék").one().id
    finally:
        s.close()

    # No filter -> both components listed.
    assert all(x in client.get("/components").text for x in ("Liszt", "Vaj"))
    # One group -> only its component.
    one = client.get("/components", params={"group_id": [str(piskota)]}).text
    assert "Liszt" in one and "Vaj" not in one
    # Both groups -> both again.
    both = client.get("/components", params={"group_id": [str(piskota), str(toltelek)]}).text
    assert "Liszt" in both and "Vaj" in both


def _seed_years(*years: int) -> None:
    import datetime as dt

    from app.db import SessionLocal
    from app.models import Customer, Offer

    s = SessionLocal()
    try:
        c = Customer(name="Év Szűrő")
        s.add(c)
        s.flush()
        for yr in years:
            s.add(
                Offer(
                    customer_id=c.id,
                    theme=f"T{yr}",
                    status="sent",
                    entry_date=dt.datetime(yr, 6, 1, tzinfo=dt.UTC),
                )
            )
        s.commit()
    finally:
        s.close()


def test_year_filter_is_multi_select(clean_db):
    """Year uses the SAME dropdown component as status (a native <select> popup
    cannot be styled, so the two looked different when opened)."""
    _seed_years(2024, 2025, 2026)

    def shown(**params):
        text = client.get("/offers", params={"f": "1", **params}).text
        return [y for y in ("T2024", "T2025", "T2026") if y in text]

    assert shown() == ["T2024", "T2025", "T2026"]  # nothing ticked -> all years
    assert shown(year="2025") == ["T2025"]
    assert shown(year=["2024", "2026"]) == ["T2024", "T2026"]
    assert shown(year="nonsense") == ["T2024", "T2025", "T2026"]  # ignored, not a 500


def test_both_filters_render_the_same_component(clean_db):
    _seed_years(2025)
    html = client.get("/offers").text
    assert 'data-filter="status"' in html
    assert 'data-filter="year"' in html
    assert '<select name="year"' not in html  # the unstylable native popup is gone


def test_year_defaults_to_the_current_year(clean_db):
    """First load shows this year's work; touching the filter overrides it."""
    import datetime as dt

    this_year = dt.datetime.now(dt.UTC).year
    _seed_years(this_year - 1, this_year)
    older, current = f"T{this_year - 1}", f"T{this_year}"

    first_load = client.get("/offers").text
    assert current in first_load and older not in first_load
    # Ticked in the dropdown, so the UI agrees with what it filtered by.
    assert f'value="{this_year}" checked' in first_load

    # An explicit submission wins — including "everything" (nothing ticked).
    both = client.get("/offers", params={"f": "1"}).text
    assert current in both and older in both


def test_year_default_falls_back_to_all_when_this_year_is_empty(clean_db):
    """A fresh January must not show an empty list."""
    import datetime as dt

    old = dt.datetime.now(dt.UTC).year - 3
    _seed_years(old)
    assert f"T{old}" in client.get("/offers").text
