"""Model-metadata sanity checks (no DB needed).

Full schema behavior (temporal pricing, EXCLUDE overlap guard, cost views,
downgrade round-trip, autogenerate no-drift) is verified against a real
Postgres 16 during development with Podman — see the project notes. These
tests guard the ORM layer in plain CI where no Postgres is present.
"""

from app.models import (
    Base,
    Component,
    ComponentPrice,
    Offer,
    OfferComponent,
    StockMovement,
)

EXPECTED_TABLES = {
    "groups",
    "components",
    "component_prices",
    "customers",
    "offers",
    "offer_components",
    "recipes",
    "recipe_items",
    "stock_movements",
    "price_sync_state",
}


def test_all_tables_present():
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_offer_cascade_deletes_lines_and_movements():
    # Deleting an offer must cascade to its lines and its stock movements,
    # but never to customers/components (§3.7).
    oc_fk = next(iter(OfferComponent.__table__.c.offer_id.foreign_keys))
    assert oc_fk.ondelete == "CASCADE"
    sm_fk = next(iter(StockMovement.__table__.c.offer_id.foreign_keys))
    assert sm_fk.ondelete == "CASCADE"
    # component_id on offer_components must NOT cascade.
    comp_fk = next(iter(OfferComponent.__table__.c.component_id.foreign_keys))
    assert comp_fk.ondelete is None


def test_component_prices_is_append_only_shape():
    # Append-only tables carry entry_date but NOT update_date (§3.0).
    cols = set(ComponentPrice.__table__.c.keys())
    assert "entry_date" in cols
    assert "update_date" not in cols
    assert "update_date" not in set(StockMovement.__table__.c.keys())


def test_mutable_tables_have_update_date():
    assert "update_date" in set(Offer.__table__.c.keys())
    assert "update_date" in set(Component.__table__.c.keys())


def test_money_and_quantity_precision():
    # Money NUMERIC(12,2); quantities NUMERIC(12,3) (§3.0).
    assert ComponentPrice.__table__.c.base_price.type.scale == 2
    assert ComponentPrice.__table__.c.base_amount.type.scale == 3
    assert OfferComponent.__table__.c.amount.type.scale == 3
