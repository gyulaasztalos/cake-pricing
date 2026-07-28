"""Készlet stock value: on-hand × unit price, floored at zero, plus the total."""

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


def _move(component_id: int, qty: str) -> None:
    from app.db import SessionLocal
    from app.models import StockMovement

    s = SessionLocal()
    try:
        s.add(StockMovement(component_id=component_id, qty_delta=Decimal(qty), reason="correction"))
        s.commit()
    finally:
        s.close()


def _rows(**params):
    """The inventory rows + total as the router computed them."""
    from app.db import SessionLocal
    from app.routers.inventory import list_inventory

    s = SessionLocal()
    try:
        resp = list_inventory(request=_FakeRequest(), session=s, **params)
        return resp.context["rows"], resp.context["total_value"]
    finally:
        s.close()


class _FakeRequest:
    """Minimal stand-in: TemplateResponse only needs .headers/.scope here."""

    headers: dict[str, str] = {}
    scope: dict = {"type": "http", "app": app, "router": app.router}
    url = None


def test_value_is_on_hand_times_unit_price(clean_db, seed_component):
    # 300 Ft per box (base_amount 1), 10 on the shelf -> 3000 Ft.
    cid = seed_component("Doboz", "Doboz", "db", "stock_item", "1", "300")
    _move(cid, "10")
    rows, total = _rows()
    assert rows[0]["value"] == Decimal("3000")
    assert total == Decimal("3000")


def test_value_respects_base_amount(clean_db, seed_component):
    # 1200 Ft per 1000 g -> 2500 g is worth 3000 Ft.
    cid = seed_component("Massza", "Burkolat", "g", "stock_item", "1000", "1200")
    _move(cid, "2500")
    rows, _ = _rows()
    assert rows[0]["value"] == Decimal("3000")


@pytest.mark.parametrize("qty", ["0", "-5"])
def test_empty_or_negative_stock_is_worth_zero(clean_db, seed_component, qty):
    cid = seed_component("Doboz", "Doboz", "db", "stock_item", "1", "300")
    if qty != "0":
        _move(cid, qty)
    rows, total = _rows()
    assert rows[0]["value"] == Decimal("0")  # never negative
    assert total == Decimal("0")


def test_total_sums_every_row(clean_db, seed_component):
    a = seed_component("Doboz", "Doboz", "db", "stock_item", "1", "300")
    b = seed_component("Talp", "Doboz", "db", "stock_item", "1", "250")
    c = seed_component("Üres", "Doboz", "db", "stock_item", "1", "999")
    _move(a, "10")  # 3000
    _move(b, "4")  # 1000
    _move(c, "-2")  # 0 — negative contributes nothing
    _, total = _rows()
    assert total == Decimal("4000")


def test_total_follows_the_active_filter(clean_db, seed_component):
    a = seed_component("Doboz", "Doboz", "db", "stock_item", "1", "300")
    b = seed_component("Talp", "Doboz", "db", "stock_item", "1", "250")
    _move(a, "10")  # 3000
    _move(b, "4")  # 1000
    _, total = _rows(q="Doboz")  # search narrows to the first item only
    assert total == Decimal("3000")


def test_page_renders_values_and_total(clean_db, seed_component):
    cid = seed_component("Doboz", "Doboz", "db", "stock_item", "1", "300")
    _move(cid, "10")
    html = client.get("/inventory").text
    assert "3\xa0000\xa0Ft" in html  # the row value
    assert "Összesen" in html  # the total row
