"""Integration tests against a real Postgres (temporal pricing, stock, portability).

Skipped automatically when DATABASE_URL is not set (plain CI without Postgres).
Locally / in the DB-enabled pipeline:
    podman run -d --name cakepg -e POSTGRES_PASSWORD=devpass -e POSTGRES_USER=cake \\
        -e POSTGRES_DB=cake-pricing -p 55432:5432 postgres:16-bookworm
    DATABASE_URL=postgresql+psycopg://cake:devpass@localhost:55432/cake-pricing \\
        uv run alembic upgrade head
    DATABASE_URL=... uv run pytest -q
"""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture
def session():
    from app.db import SessionLocal

    s = SessionLocal()
    yield s
    s.rollback()
    s.close()


def _clean(session):
    # Children-first wipe (respects FKs), keep it simple for a test DB.
    for tbl in [
        "stock_movements", "offer_components", "recipe_items", "offers",
        "recipes", "component_prices", "components", "customers",
    ]:
        session.execute(text(f"DELETE FROM {tbl}"))
    session.execute(text("DELETE FROM groups"))
    session.commit()


def test_temporal_pricing_old_offer_not_corrupted(session):
    from app.models import Component, ComponentPrice, Group
    from app.services.pricing import price_line

    _clean(session)
    g = Group(name="G", sort_order=1)
    session.add(g)
    session.flush()
    c = Component(name="Liszt", group_id=g.id, unit="g", type="ingredient")
    session.add(c)
    session.flush()
    # old window then current window (half-open)
    session.add(ComponentPrice(component_id=c.id, base_amount=Decimal("1000"), base_price=Decimal("165"),
                               effective_date=dt.datetime(2025, 6, 1, tzinfo=dt.UTC),
                               expiration_date=dt.datetime(2026, 1, 2, tzinfo=dt.UTC)))
    session.add(ComponentPrice(component_id=c.id, base_amount=Decimal("1000"), base_price=Decimal("187"),
                               effective_date=dt.datetime(2026, 1, 2, tzinfo=dt.UTC)))
    session.flush()

    old = price_line(session, c.id, Decimal("1000"), dt.datetime(2025, 8, 1, tzinfo=dt.UTC))
    new = price_line(session, c.id, Decimal("1000"), dt.datetime(2026, 7, 1, tzinfo=dt.UTC))
    assert old.line_price == Decimal("165")
    assert new.line_price == Decimal("187")
    session.rollback()


def test_overlapping_price_window_rejected(session):
    from sqlalchemy.exc import IntegrityError

    from app.models import Component, ComponentPrice, Group

    _clean(session)
    g = Group(name="G", sort_order=1)
    session.add(g)
    session.flush()
    c = Component(name="X", group_id=g.id)
    session.add(c)
    session.flush()
    session.add(ComponentPrice(component_id=c.id, base_amount=Decimal("1"), base_price=Decimal("10"),
                               effective_date=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))
    session.flush()
    session.add(ComponentPrice(component_id=c.id, base_amount=Decimal("1"), base_price=Decimal("20"),
                               effective_date=dt.datetime(2026, 3, 1, tzinfo=dt.UTC)))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_earliest_price_fallback(session):
    from app.models import Component, ComponentPrice, Group
    from app.services.pricing import price_line

    _clean(session)
    g = Group(name="G", sort_order=1)
    session.add(g)
    session.flush()
    c = Component(name="Y", group_id=g.id)
    session.add(c)
    session.flush()
    session.add(ComponentPrice(component_id=c.id, base_amount=Decimal("100"), base_price=Decimal("500"),
                               effective_date=dt.datetime(2026, 7, 1, tzinfo=dt.UTC)))
    session.flush()
    # as_of BEFORE the only window -> fallback to earliest price
    p = price_line(session, c.id, Decimal("100"), dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    assert p.line_price == Decimal("500")
    assert p.used_fallback_price is True
    session.rollback()


def test_portability_round_trip(session):
    from app.models import Component, Customer, Group
    from app.services import portability

    _clean(session)
    g = Group(name="Alap", sort_order=10)
    session.add(g)
    session.flush()
    session.add(Component(name="Vaj", group_id=g.id, unit="g"))
    session.add(Customer(name="Teszt Elek"))
    session.commit()

    bundle = portability.export_bundle(session)
    assert bundle["schema_version"] == 1
    counts = portability.import_bundle(session, bundle, replace=True)
    session.commit()
    assert counts["components"] == 1
    assert session.scalar(text("select count(*) from customers")) == 1
    # UTF-8 preserved
    assert session.scalar(text("select name from customers")) == "Teszt Elek"
    session.rollback()
