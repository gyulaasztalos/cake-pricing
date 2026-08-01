"""Business profit: per-offer %, the Settings default, and the Kész money split."""

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
        c = Customer(name="Nyereség Teszt")
        s.add(c)
        s.commit()
        return c.id
    finally:
        s.close()


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


def _create(cid: int, **form: str) -> int:
    from app.db import SessionLocal
    from app.models import Offer

    r = client.post(
        "/offers", data={"customer_id": str(cid), "status": "draft", **form}, follow_redirects=False
    )
    assert r.status_code == 303, r.text
    s = SessionLocal()
    try:
        return s.query(Offer).order_by(Offer.id.desc()).first().id
    finally:
        s.close()


# --- the profit % is DERIVED, never stored -----------------------------------


def test_profit_pct_is_not_a_column(clean_db):
    """Like cost, the % is computed — storing it could drift from the price."""
    from app.models import Offer

    assert "profit_pct" not in Offer.__table__.columns


def test_existing_offer_shows_its_REAL_implied_margin(clean_db, seed_component):
    """A priced offer must show the margin it actually has, not the default."""
    from app.db import SessionLocal
    from app.models import Offer, OfferComponent

    labour = seed_component("Munkadíj", "Alap", "db", "service", "1", "10000")
    cid = _customer()
    s = SessionLocal()
    try:
        o = Offer(customer_id=cid, status="draft", final_price=Decimal("15000"))
        s.add(o)
        s.flush()
        s.add(OfferComponent(offer_id=o.id, component_id=labour, amount=Decimal("1")))
        s.commit()
        oid = o.id
    finally:
        s.close()

    # cost base 10 000, price 15 000 -> 50%, NOT the 10% default.
    html = client.get(f"/offers/{oid}/edit").text
    assert 'id="profit-pct"' in html
    assert 'value="50"' in html
    assert 'value="15000"' in html


def test_final_price_and_paid_accept_a_comma(clean_db):
    oid = _create(_customer(), final_price="11 250,50", paid="11 300,25")
    o = _offer(oid)
    assert o.final_price == Decimal("11250.50")
    assert o.paid == Decimal("11300.25")


def test_a_posted_profit_pct_is_ignored_not_persisted(clean_db):
    """The field is a client-side control; only the price is a decision."""
    oid = _create(_customer(), final_price="11000", profit_pct="999")
    assert _offer(oid).final_price == Decimal("11000")


# --- the Settings default -----------------------------------------------------


def test_default_pct_prefills_a_new_offer_and_is_editable(clean_db):
    assert 'id="profit-pct"' in client.get("/offers/new").text
    assert 'value="10"' in client.get("/offers/new").text

    r = client.post("/settings/profit", data={"default_profit_pct": "17,5"}, follow_redirects=False)
    assert r.status_code == 303
    assert 'value="17.5"' in client.get("/offers/new").text  # new offers follow it
    assert 'value="17.5"' in client.get("/settings").text  # and it round-trips


def test_changing_the_default_leaves_existing_offers_alone(clean_db, seed_component):
    """A priced offer keeps showing ITS margin; the default only seeds new ones."""
    from app.db import SessionLocal
    from app.models import Offer, OfferComponent

    labour = seed_component("Munkadíj", "Alap", "db", "service", "1", "10000")
    s = SessionLocal()
    try:
        o = Offer(customer_id=_customer(), status="draft", final_price=Decimal("12000"))
        s.add(o)
        s.flush()
        s.add(OfferComponent(offer_id=o.id, component_id=labour, amount=Decimal("1")))
        s.commit()
        oid = o.id
    finally:
        s.close()

    client.post("/settings/profit", data={"default_profit_pct": "30"}, follow_redirects=False)
    html = client.get(f"/offers/{oid}/edit").text
    assert 'value="20"' in html  # derived 12 000/10 000 − 1, NOT the new 30% default
    assert 'value="12000"' in html


# --- the Kész money split -----------------------------------------------------


def test_done_split_reports_alap_components_tip_and_profit(clean_db, seed_component):
    from app.db import SessionLocal
    from app.models import Offer, OfferComponent
    from app.services import stats as stats_svc

    labour = seed_component("Munkadíj", "Alap", "db", "service", "1", "5000")
    overhead = seed_component("Rezsi+amortizáció", "Alap", "db", "service", "1", "1000")
    flour = seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "2000")
    cid = _customer()

    s = SessionLocal()
    try:
        # cost base = 5000 + 1000 + 2000 = 8000; quoted 9000; paid 9500 (500 tip).
        o = Offer(
            customer_id=cid,
            status="done",
            final_price=Decimal("9000"),
            paid=Decimal("9500"),
        )
        s.add(o)
        s.flush()
        for comp, amt in ((labour, "1"), (overhead, "1"), (flour, "1000")):
            s.add(OfferComponent(offer_id=o.id, component_id=comp, amount=Decimal(amt)))
        # A still-open offer must NOT count towards the finished-work split.
        s.add(Offer(customer_id=cid, status="accepted", final_price=Decimal("99999")))
        s.commit()
    finally:
        s.close()

    s2 = SessionLocal()
    try:
        d = stats_svc.collect(s2, None).done_split
    finally:
        s2.close()

    assert dict(d.base_rows) == {"Munkadíj": Decimal("5000"), "Rezsi+amortizáció": Decimal("1000")}
    assert d.materials == Decimal("2000")
    assert d.tip == Decimal("500")
    assert d.profit == Decimal("1000")  # 9000 quoted − 8000 cost base


def test_underpayment_is_not_a_negative_tip(clean_db, seed_component):
    from app.db import SessionLocal
    from app.models import Offer
    from app.services import stats as stats_svc

    s = SessionLocal()
    try:
        s.add(
            Offer(
                customer_id=_customer(),
                status="done",
                final_price=Decimal("9000"),
                paid=Decimal("8000"),
            )
        )
        s.commit()
    finally:
        s.close()

    s2 = SessionLocal()
    try:
        assert stats_svc.collect(s2, None).done_split.tip == Decimal("0")
    finally:
        s2.close()
