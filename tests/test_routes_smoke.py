"""Smoke tests: every GET route must render (200), and key list pages must not 500.

This is the guard that was missing when /customers and /recipes regressed to
500 after a refactor — a plain page load with no test. Runs against Postgres
(gated on DATABASE_URL) with a minimal seeded dataset so list/detail queries
execute for real.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture(scope="module")
def seeded():
    """One offer with a customer + a component (+price) + a template, so list and
    detail pages have rows to render."""
    from app.db import SessionLocal
    from app.models import (
        Component,
        ComponentPrice,
        Customer,
        Group,
        Offer,
        OfferComponent,
        Recipe,
        RecipeItem,
    )

    s = SessionLocal()
    # ensure at least one group exists (migration seeds 6, but be self-contained)
    group = s.query(Group).order_by(Group.id).first()
    if group is None:
        group = Group(name="Teszt")
        s.add(group)
        s.flush()
    comp = Component(name="Smoke Liszt", group_id=group.id, unit="g", type="ingredient")
    s.add(comp)
    s.flush()
    s.add(
        ComponentPrice(component_id=comp.id, base_amount=Decimal("1000"), base_price=Decimal("200"))
    )
    cust = Customer(name="Smoke Ügyfél")
    s.add(cust)
    s.flush()
    offer = Offer(customer_id=cust.id, theme="Smoke", flavor="Vanília", status="draft")
    s.add(offer)
    s.flush()
    s.add(OfferComponent(offer_id=offer.id, component_id=comp.id, amount=Decimal("500")))
    recipe = Recipe(name="Smoke recept")
    s.add(recipe)
    s.flush()
    s.add(RecipeItem(recipe_id=recipe.id, component_id=comp.id, amount=Decimal("500")))
    s.commit()
    ids = {"offer": offer.id, "customer": cust.id, "component": comp.id, "recipe": recipe.id}
    s.close()
    return ids


# Static GET routes that must always render.
STATIC_ROUTES = [
    "/",
    "/offers",
    "/offers/new",
    "/customers",
    "/customers/new",
    "/customers/quick-new",
    "/components",
    "/components/new",
    "/components/quick-new",
    "/groups",
    "/groups/new",
    "/recipes",
    "/inventory",
    "/inventory/receive",
    "/settings",
    "/healthz",
    "/readyz",
]


@pytest.mark.parametrize("path", STATIC_ROUTES)
def test_static_get_routes_ok(client, seeded, path):
    r = client.get(path, follow_redirects=False)
    assert r.status_code in (200, 303, 307), f"{path} -> {r.status_code}\n{r.text[:300]}"


def test_detail_and_edit_routes_ok(client, seeded):
    checks = [
        f"/offers/detail/{seeded['offer']}",
        f"/offers/{seeded['offer']}/edit",
        f"/offers/{seeded['offer']}/delete",
        f"/customers/detail/{seeded['customer']}",
        f"/customers/{seeded['customer']}/edit",
        f"/customers/{seeded['customer']}/delete",
        f"/components/detail/{seeded['component']}",
        f"/components/{seeded['component']}/edit",
        f"/recipes/detail/{seeded['recipe']}",
        f"/recipes/{seeded['recipe']}/edit",
        f"/recipes/{seeded['recipe']}/delete",
        f"/inventory/detail/{seeded['component']}",
    ]
    for path in checks:
        r = client.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}\n{r.text[:300]}"


def test_missing_id_returns_404(client, seeded):
    # get_or_404 must yield 404 (not 500) for unknown ids.
    for path in ["/offers/999999/edit", "/customers/999999/edit", "/components/999999/edit"]:
        r = client.get(path)
        assert r.status_code == 404, f"{path} -> {r.status_code}"
