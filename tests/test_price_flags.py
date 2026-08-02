"""Price-confidence badges: approximate ($ amber) vs missing price (⚠ red)."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.pricing import price_from_rows

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def test_no_price_rows_is_flagged_not_silently_zero():
    """Pure unit: a component with no price history must be marked, not just 0."""
    line = price_from_rows([], Decimal("1"), dt.datetime.now(dt.UTC), component_id=1)
    assert line.line_price == Decimal("0")
    assert line.price_missing is True
    assert line.used_fallback_price is False


def _offer_with_three_lines() -> int:
    """One normally-priced line, one priced from a fallback, one with no price."""
    from app.db import SessionLocal
    from app.models import Component, ComponentPrice, Customer, Group, Offer, OfferComponent

    now = dt.datetime.now(dt.UTC)
    s = SessionLocal()
    try:
        gid = s.query(Group).filter_by(name="Piskóta").one().id

        def _comp(name: str) -> int:
            c = Component(name=name, group_id=gid, unit="db", type="ingredient")
            s.add(c)
            s.flush()
            return c.id

        ok = _comp("Rendben")
        s.add(
            ComponentPrice(
                component_id=ok,
                base_amount=Decimal("1"),
                base_price=Decimal("100"),
                effective_date=now - dt.timedelta(days=30),  # covers the offer
            )
        )
        late = _comp("Késői ár")
        s.add(
            ComponentPrice(
                component_id=late,
                base_amount=Decimal("1"),
                base_price=Decimal("200"),
                effective_date=now + dt.timedelta(days=5),  # starts AFTER the offer
            )
        )
        unpriced = _comp("Nincs ár")  # no ComponentPrice at all

        cust = Customer(name="Jelzés Teszt")
        s.add(cust)
        s.flush()
        offer = Offer(customer_id=cust.id, status="draft", entry_date=now)
        s.add(offer)
        s.flush()
        for cid in (ok, late, unpriced):
            s.add(OfferComponent(offer_id=offer.id, component_id=cid, amount=Decimal("1")))
        s.commit()
        return offer.id
    finally:
        s.close()


def _line_html(html: str, name: str) -> str:
    import re

    for li in re.findall(r"<li>.*?</li>", html, re.S):
        if name in li:
            return li
    raise AssertionError(f"line {name!r} not rendered")


def test_detail_badges_distinguish_the_three_cases(clean_db):
    html = client.get(f"/offers/detail/{_offer_with_three_lines()}").text

    # A properly covered price carries no badge at all.
    normal = _line_html(html, "Rendben")
    assert "is-warn" not in normal and "is-error" not in normal

    # Priced from the earliest row because none covered the offer date → amber $.
    fallback = _line_html(html, "Késői ár")
    assert "is-warn" in fallback and "circle-dollar-sign" in fallback

    # No price at all → RED, and a different ICON so it is not colour-only.
    missing = _line_html(html, "Nincs ár")
    assert "is-error" in missing and "triangle-alert" in missing


def test_badges_carry_an_explanatory_tooltip(clean_db):
    """The old bare "ár?" text was cryptic; both badges must explain themselves."""
    html = client.get(f"/offers/detail/{_offer_with_three_lines()}").text
    assert 'title="Erre az ajánlatra nem volt érvényes ár' in html
    assert 'title="Ehhez az összetevőhöz nincs rögzített ár' in html
    assert ">ár?<" not in html  # the untranslated literal is gone


def test_the_edit_form_warns_too(clean_db):
    """The form is where the chef can actually fix a missing price."""
    oid = _offer_with_three_lines()
    html = client.get(f"/offers/{oid}/edit").text
    assert "is-error" in html and "triangle-alert" in html
    assert "is-warn" in html and "circle-dollar-sign" in html
