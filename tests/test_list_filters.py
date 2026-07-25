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
