"""Piskóta (sponge): arrives via intake, editable by the chef, grouped in stats."""

from __future__ import annotations

import datetime as dt
import os

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture
def token(monkeypatch):
    """Enable the intake API for this test (mirrors tests/test_intake.py)."""
    monkeypatch.setattr(settings, "intake_token", "test-intake-token")
    return {"Authorization": "Bearer test-intake-token"}


def _intake(sponge: str | None, headers: dict) -> dict:
    body = {
        "name": "Piskóta Teszt",
        "email": "piskota@example.com",
        "due_date": "2026-12-24",
        "description": "Kérek egy tortát.",
        "request_date": dt.datetime.now(dt.UTC).isoformat(),
    }
    if sponge is not None:
        body["sponge"] = sponge
    r = client.post("/api/intake/offers", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _offer(offer_id: int):
    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        o = s.get(Offer, offer_id)
        s.expunge(o)
        return o
    finally:
        s.close()


def test_intake_stores_the_sponge(clean_db, token):
    oid = _intake("Fekete kakaós", token)["offer_id"]
    assert _offer(oid).sponge == "Fekete kakaós"


def test_intake_without_sponge_leaves_it_null(clean_db, token):
    oid = _intake(None, token)["offer_id"]
    assert _offer(oid).sponge is None


def test_chef_can_set_and_change_the_sponge(clean_db, token):
    oid = _intake(None, token)["offer_id"]
    cid = _offer(oid).customer_id

    r = client.post(
        f"/offers/{oid}",
        data={"customer_id": str(cid), "status": "draft", "sponge": "Mákos"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _offer(oid).sponge == "Mákos"

    # Changing it sticks; clearing it blanks the column rather than storing "".
    client.post(
        f"/offers/{oid}",
        data={"customer_id": str(cid), "status": "draft", "sponge": "Diós"},
        follow_redirects=False,
    )
    assert _offer(oid).sponge == "Diós"
    client.post(
        f"/offers/{oid}",
        data={"customer_id": str(cid), "status": "draft", "sponge": "   "},
        follow_redirects=False,
    )
    assert _offer(oid).sponge is None


def test_offer_form_and_list_show_the_sponge(clean_db, token):
    oid = _intake("Pisztáciás", token)["offer_id"]
    assert 'name="sponge" value="Pisztáciás"' in client.get(f"/offers/{oid}/edit").text
    assert "Pisztáciás" in client.get("/offers", params={"f": "1"}).text


def test_stats_group_offers_by_sponge(clean_db):
    from app.db import SessionLocal
    from app.models import Customer, Offer
    from app.services import stats as stats_svc

    s = SessionLocal()
    try:
        c = Customer(name="Stat")
        s.add(c)
        s.flush()
        for sponge in ("Kakaós", "Kakaós", "Mákos"):
            s.add(Offer(customer_id=c.id, status="done", sponge=sponge))
        s.add(Offer(customer_id=c.id, status="done", sponge=None))  # ignored
        s.commit()
    finally:
        s.close()

    s2 = SessionLocal()
    try:
        top = stats_svc.collect(s2, None).top_sponges
    finally:
        s2.close()
    assert top == [("Kakaós", 2), ("Mákos", 1)]
