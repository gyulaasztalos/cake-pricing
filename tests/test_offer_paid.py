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
    # Fizetve is no longer a form field — it is the sum of the payment lines. A
    # `paid=` shorthand becomes a single payment line, so these tests keep
    # exercising the auto-status rule, which still reads the total.
    paid = form.pop("paid", None)
    data = {"customer_id": str(cid), "status": "sent", **form}
    if paid:
        data |= {"payment_method": "cash", "payment_amount": paid}
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
            "payment_method": "cash",
            "payment_amount": "5000",
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
            "payment_method": "cash",
            "payment_amount": "10000",
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


def test_cancelling_while_recording_the_kept_deposit_stays_cancelled(clean_db):
    """The trap this status walks straight into.

    Auto-status fires whenever Fizetve CHANGES — which is precisely the save where
    the chef marks the offer Lemondás and records the deposit she kept. Without the
    guard the offer would bounce back to Előlegezve (or Kész, if the customer had
    already paid in full before pulling out), losing the cancellation.
    """
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    r = client.post(
        f"/offers/{oid}",
        data={
            "customer_id": str(cid),
            "status": "cancelled",
            "final_price": "20000",
            "payment_method": "cash",
            "payment_amount": "5000",  # the deposit she keeps — a CHANGE, so auto-status runs
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert _status(oid) == "cancelled"


def test_cancelling_after_full_payment_stays_cancelled(clean_db):
    """paid >= final would otherwise mean 'done'; a cancellation still outranks it."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    client.post(
        f"/offers/{oid}",
        data={
            "customer_id": str(cid),
            "status": "cancelled",
            "final_price": "20000",
            "payment_method": "cash",
            "payment_amount": "20000",
        },
        follow_redirects=False,
    )
    assert _status(oid) == "cancelled"


def test_a_new_offer_can_be_created_already_cancelled(clean_db):
    """Creation runs auto-status unconditionally, so it needs the guard too."""
    assert (
        _status(_create(_customer(), final_price="20000", paid="5000", status="cancelled"))
        == "cancelled"
    )


# --- payment lines -------------------------------------------------------------


def _payments(offer_id: int) -> tuple[Decimal | None, list[tuple[str, Decimal]]]:
    """(Fizetve, [(method, amount), ...]) as stored."""
    from app.db import SessionLocal
    from app.models import Offer

    s = SessionLocal()
    try:
        o = s.get(Offer, offer_id)
        return o.paid, [(p.method, p.amount) for p in o.payments]
    finally:
        s.close()


def _save(oid: int, cid: int, methods: list[str], amounts: list[str], **extra: str) -> None:
    r = client.post(
        f"/offers/{oid}",
        data={
            "customer_id": str(cid),
            "status": "accepted",
            "final_price": "20000",
            "payment_method": methods,
            "payment_amount": amounts,
            **extra,
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_payment_lines_sum_into_fizetve(clean_db):
    """A transfer deposit, then cash twice — the same method may repeat."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["transfer", "cash", "cash"], ["5 000", "10000", "5000"])
    paid, lines = _payments(oid)
    assert paid == Decimal("20000")
    assert lines == [
        ("transfer", Decimal("5000")),
        ("cash", Decimal("10000")),
        ("cash", Decimal("5000")),
    ]
    assert _status(oid) == "done"  # the TOTAL still drives the status


def test_only_the_three_methods_and_real_amounts_are_kept(clean_db):
    """A tampered method, a blank row and a zero are dropped — none may reach the
    DB CHECK as a 500, and none may count towards Fizetve."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["paypal", "cash", "cash", "revolut"], ["9999", "", "0", "3000"])
    paid, lines = _payments(oid)
    assert lines == [("revolut", Decimal("3000"))]
    assert paid == Decimal("3000")


def test_a_posted_fizetve_is_ignored(clean_db):
    """Fizetve is derived; the server is its only writer, never the form."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, [], [], paid="99999")
    assert _payments(oid) == (None, [])


def test_removing_every_payment_clears_fizetve_to_null(clean_db):
    """No lines means nothing recorded — NULL, not 0. The auto-status rule treats
    those differently (0 would read as 'paid nothing' and mark it Előlegezve)."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["cash"], ["5000"])
    _save(oid, cid, [], [])
    assert _payments(oid) == (None, [])


def test_resplitting_the_same_total_is_not_a_change(clean_db):
    """Moving 5 000 from cash to a transfer leaves Fizetve unchanged, so the
    chef's manually chosen status must stand — the gate compares the total."""
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["cash"], ["5000"])  # -> deposit (auto)
    _save(oid, cid, ["transfer"], ["5000"], status="accepted")
    assert _status(oid) == "accepted"


def test_the_edit_form_shows_the_lines_and_a_read_only_fizetve(clean_db):
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["revolut"], ["7000"])
    html = client.get(f"/offers/{oid}/edit").text
    assert 'value="revolut" selected' in html
    assert 'name="payment_amount"' in html and 'value="7000"' in html
    # Fizetve is display-only: read-only, and no name, so it is never submitted.
    paid_input = html[html.index('id="paid"') - 40 : html.index('id="paid"') + 200]
    assert "readonly" in paid_input and 'name="paid"' not in html


def test_the_detail_view_lists_the_methods(clean_db):
    cid = _customer()
    oid = _create(cid, final_price="20000", status="accepted")
    _save(oid, cid, ["transfer", "cash"], ["5000", "15000"])
    html = client.get(f"/offers/detail/{oid}").text
    assert "Utalás" in html and "Készpénz" in html


def test_payment_methods_are_listed_alphabetically(clean_db):
    """By the label the chef reads, not the slug."""
    html = client.get("/offers/new").text
    row = html[html.index('id="payment-line-tpl"') :]
    positions = [row.index(label) for label in ("Készpénz", "Revolut", "Utalás")]
    assert positions == sorted(positions)
