"""Fizetve (paid) → auto-status, the change-only gate, and paid-preferring stats."""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def _customer() -> int:
    from app.db import SessionLocal
    from app.models import Customer

    s = SessionLocal()
    try:
        c = Customer(name="Fizető Teszt")
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


def _status(offer_id: int) -> str:
    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        return s.get(Offer, offer_id).status
    finally:
        s.close()


def _create(cid: int, **form: str) -> int:
    data = {"customer_id": str(cid), "status": "sent", **form}
    r = client.post("/offers", data=data, follow_redirects=False)
    assert r.status_code == 303
    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        return s.query(Offer).order_by(Offer.id.desc()).first().id
    finally:
        s.close()


def test_paid_below_final_sets_deposit(clean_db):
    oid = _create(_customer(), final_price="10000", paid="5000")
    assert _status(oid) == "deposit"


def test_paid_at_or_above_final_sets_done(clean_db):
    cid = _customer()
    assert _status(_create(cid, final_price="10000", paid="10000")) == "done"  # equal
    assert _status(_create(cid, final_price="10000", paid="12000")) == "done"  # above


def test_no_paid_keeps_chosen_status(clean_db):
    oid = _create(_customer(), final_price="10000", status="accepted")
    assert _status(oid) == "accepted"


def test_resave_with_same_paid_keeps_manual_status(clean_db):
    cid = _customer()
    oid = _create(cid, final_price="10000", paid="5000")
    assert _status(oid) == "deposit"
    # Re-save picking a status manually, paid UNCHANGED → manual choice stands.
    r = client.post(
        f"/offers/{oid}",
        data={
            "customer_id": str(cid),
            "status": "accepted",
            "final_price": "10000",
            "paid": "5000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _status(oid) == "accepted"


def test_changing_paid_retriggers_auto_status(clean_db):
    cid = _customer()
    oid = _create(cid, final_price="10000", paid="5000")  # deposit
    # Paid now meets the final price; auto 'done' overrides the submitted 'accepted'.
    r = client.post(
        f"/offers/{oid}",
        data={
            "customer_id": str(cid),
            "status": "accepted",
            "final_price": "10000",
            "paid": "10000",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _status(oid) == "done"


def test_stats_revenue_prefers_paid(clean_db):
    from app.db import SessionLocal
    from app.models import Customer, Offer
    from app.services import stats as stats_svc

    s = SessionLocal()
    try:
        c = Customer(name="Stat")
        s.add(c)
        s.flush()
        # A won (done) offer quoted at 10000 but only 8000 recorded as paid.
        s.add(
            Offer(
                customer_id=c.id,
                status="done",
                final_price=Decimal("10000"),
                paid=Decimal("8000"),
            )
        )
        s.commit()
    finally:
        s.close()

    s2 = SessionLocal()
    try:
        kpis = stats_svc.collect(s2, None).kpis
        assert kpis.revenue == Decimal("8000")  # paid preferred over final_price
    finally:
        s2.close()
