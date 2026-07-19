"""Customer-intake API (§8a): auth, creation semantics, entry_date lifecycle."""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

PAYLOAD = {
    "name": "Kovács Éva",
    "email": "eva@example.com",
    "phone": "+36 30 123 4567",
    "due_date": (dt.date.today() + dt.timedelta(days=14)).isoformat(),
    "theme": "Születésnapi torta",
    "flavor": "Oreo",
    "portions": 16,
    "description": "16 szeletes epres torta, vintage szív díszítéssel.",
    "locale": "hu",
    "request_date": dt.datetime.now(dt.UTC).isoformat(),
}


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(settings, "intake_token", "test-intake-token")
    return {"Authorization": "Bearer test-intake-token"}


def test_intake_disabled_without_token_config(monkeypatch):
    monkeypatch.setattr(settings, "intake_token", "")
    r = client.post("/api/intake/offers", json=PAYLOAD)
    assert r.status_code == 503


def test_intake_rejects_bad_token(token):
    r = client.post(
        "/api/intake/offers", json=PAYLOAD, headers={"Authorization": "Bearer wrong"}
    )
    assert r.status_code == 401
    assert client.post("/api/intake/offers", json=PAYLOAD).status_code == 401


def test_intake_creates_external_unpriced_draft(clean_db, token):
    r = client.post("/api/intake/offers", json=PAYLOAD, headers=token)
    assert r.status_code == 201, r.text
    ids = r.json()

    from app.db import SessionLocal
    from app.models import Customer, Offer

    s = SessionLocal()
    try:
        offer = s.get(Offer, ids["offer_id"])
        customer = s.get(Customer, ids["customer_id"])
        assert offer.source == "external"
        assert offer.status == "draft"
        assert offer.entry_date is None  # §8a: priced when the chef first saves
        assert offer.request_date is not None
        assert offer.portions == 16
        assert offer.theme == "Születésnapi torta"
        assert offer.flavor == "Oreo"
        assert "epres torta" in offer.notes
        assert customer.name == "Kovács Éva"
        assert "eva@example.com" in customer.contact
    finally:
        s.close()


def test_intake_dedupes_customer_by_email(clean_db, token):
    r1 = client.post("/api/intake/offers", json=PAYLOAD, headers=token)
    r2 = client.post("/api/intake/offers", json={**PAYLOAD, "name": "Éva K."}, headers=token)
    assert r1.json()["customer_id"] == r2.json()["customer_id"]
    assert r1.json()["offer_id"] != r2.json()["offer_id"]


def test_intake_dedup_does_not_substring_match(clean_db, token):
    # ann@x must NOT attach to an existing joann@x customer (anchored match).
    r1 = client.post(
        "/api/intake/offers", json={**PAYLOAD, "email": "joann@example.com"}, headers=token
    )
    r2 = client.post(
        "/api/intake/offers", json={**PAYLOAD, "email": "ann@example.com"}, headers=token
    )
    assert r1.json()["customer_id"] != r2.json()["customer_id"]


def test_offer_list_renders_with_unpriced_external_draft(clean_db, token):
    # Regression: NULL entry_date crashed the year-filter dropdown (int(None)).
    client.post("/api/intake/offers", json=PAYLOAD, headers=token)
    r = client.get("/offers")
    assert r.status_code == 200
    assert "külső ajánlatkérés" in r.text  # badge visible in the list


def test_first_chef_save_sets_entry_date(clean_db, token):
    ids = client.post("/api/intake/offers", json=PAYLOAD, headers=token).json()

    # The chef opens and saves the draft via the normal offer form.
    r = client.post(
        f"/offers/{ids['offer_id']}",
        data={"customer_id": str(ids["customer_id"]), "status": "draft"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        offer = s.get(Offer, ids["offer_id"])
        assert offer.entry_date is not None  # pricing date locked at first save
        first_entry = offer.entry_date
    finally:
        s.close()

    # A second save must NOT move it (immutable once set, §3.4).
    client.post(
        f"/offers/{ids['offer_id']}",
        data={"customer_id": str(ids["customer_id"]), "status": "sent"},
        follow_redirects=False,
    )
    s = SessionLocal()
    try:
        assert s.get(Offer, ids["offer_id"]).entry_date == first_entry
    finally:
        s.close()
