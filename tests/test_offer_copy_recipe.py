"""Copy an offer, and Save-as-Recept (Alap-group exclusion + empty guard)."""

from __future__ import annotations

import json
import os
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="requires a Postgres DATABASE_URL"
)


def _make_offer(flavor: str, lines: list[tuple[int, Decimal]]) -> int:
    from app.db import SessionLocal
    from app.models import Customer, Offer
    from app.services import offers as offer_svc

    s = SessionLocal()
    try:
        cust = Customer(name="Teszt Ügyfél")
        s.add(cust)
        s.flush()
        offer = Offer(
            customer_id=cust.id,
            theme="Szülinap",
            flavor=flavor,
            status="sent",
            notes="belső jegyzet",
        )
        s.add(offer)
        s.flush()
        offer_svc.save_offer_lines(s, offer, lines)
        s.commit()
        return offer.id
    finally:
        s.close()


# --- copy --------------------------------------------------------------------


def test_copy_offer_prefills_flavor_and_lines_but_not_metadata(clean_db, seed_component):
    ing = seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")
    oid = _make_offer("epres", [(ing, Decimal("500"))])

    r = client.get(f"/offers/{oid}/copy")
    assert r.status_code == 200
    # It's the NEW-offer form (posts to POST /offers — a fresh draft, not an edit).
    assert 'action="/offers"' in r.text
    # Flavor is copied; the source line is present and selected with its amount.
    assert 'name="flavor" value="epres"' in r.text
    assert f'value="{ing}" data-unit="g" selected' in r.text
    assert 'name="amount"\n         value="500"' in r.text or 'value="500"' in r.text
    # Theme, notes, and status metadata are NOT carried over; status resets to draft.
    assert 'name="theme" value=""' in r.text
    assert "belső jegyzet" not in r.text
    assert '<option value="draft" selected>' in r.text


# --- save as recipe ----------------------------------------------------------


def test_save_as_recipe_strips_alap_group_and_keeps_the_rest(clean_db, seed_component):
    labor = seed_component("Munkadíj", "Alap", "db", "service", "1", "5000")
    ing = seed_component("Liszt", "Piskóta", "g", "ingredient", "1000", "200")

    r = client.post(
        "/offers/save-as-recipe",
        data={
            "recipe_name": "12 szeletes",
            "component_id": [str(labor), str(ing)],
            "amount": ["1", "500"],
        },
    )
    assert r.status_code == 200
    assert r.text == ""  # empty body on success
    trigger = json.loads(r.headers["HX-Trigger"])
    assert "cpRecipeSaved" in trigger

    from app.db import SessionLocal
    from app.models import Recipe, RecipeItem

    s = SessionLocal()
    try:
        recipe = s.query(Recipe).filter_by(name="12 szeletes").one()
        items = s.query(RecipeItem).filter_by(recipe_id=recipe.id).all()
        # Only the ingredient survived — the Alap-group labor line was stripped.
        assert [it.component_id for it in items] == [ing]
    finally:
        s.close()


def test_save_as_recipe_errors_when_only_alap_lines(clean_db, seed_component):
    labor = seed_component("Munkadíj", "Alap", "db", "service", "1", "5000")

    r = client.post(
        "/offers/save-as-recipe",
        data={"recipe_name": "Csak alap", "component_id": [str(labor)], "amount": ["1"]},
    )
    assert r.status_code == 200
    assert "HX-Trigger" not in r.headers  # nothing saved → no success trigger
    assert "Nincs menthető tétel" in r.text  # inline error, dialog stays open

    from app.db import SessionLocal
    from app.models import Recipe

    s = SessionLocal()
    try:
        assert s.query(Recipe).filter_by(name="Csak alap").count() == 0
    finally:
        s.close()
