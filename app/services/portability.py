"""Export / import the whole dataset as a versioned JSON bundle (§5.2).

This is the ONE DB-writing import path. The xlsx converter (scripts/xlsx_to_bundle.py)
emits the same bundle format, so the initial data load reuses this importer (§6).

Bundle shape:
    {
      "schema_version": 1,
      "exported_at": "<iso8601 or null>",
      "tables": {
        "groups": [ {...}, ... ],
        "components": [...], "component_prices": [...],
        "customers": [...], "offers": [...], "offer_components": [...],
        "recipes": [...], "recipe_items": [...], "stock_movements": [...]
      }
    }
Rows carry their own ids; import can either preserve ids (restore) or remap
(merge). MVP does a full REPLACE restore preserving ids — simplest and matches
"my own backup/restore". UTF-8 throughout.
"""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models import (
    Component,
    ComponentPrice,
    Customer,
    Group,
    Offer,
    OfferComponent,
    Recipe,
    RecipeItem,
    StockMovement,
)

SCHEMA_VERSION = 1

# Export/import order respects FK dependencies (parents first).
_ORDER = [
    ("groups", Group),
    ("components", Component),
    ("component_prices", ComponentPrice),
    ("customers", Customer),
    ("offers", Offer),
    ("offer_components", OfferComponent),
    ("recipes", Recipe),
    ("recipe_items", RecipeItem),
    ("stock_movements", StockMovement),
]
_BY_NAME = {name: model for name, model in _ORDER}


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    return value


def _row_to_dict(obj: Any) -> dict[str, Any]:
    return {c.name: _to_jsonable(getattr(obj, c.name)) for c in obj.__table__.columns}


def export_bundle(session: Session, exported_at: str | None = None) -> dict[str, Any]:
    tables: dict[str, list[dict]] = {}
    for name, model in _ORDER:
        rows = session.scalars(select(model).order_by(model.id))
        tables[name] = [_row_to_dict(r) for r in rows]
    return {"schema_version": SCHEMA_VERSION, "exported_at": exported_at, "tables": tables}


def export_json(session: Session, exported_at: str | None = None) -> str:
    return json.dumps(export_bundle(session, exported_at), ensure_ascii=False, indent=2)


def _coerce(model: Any, row: dict[str, Any]) -> dict[str, Any]:
    """Coerce JSON scalars back to python types per column (Decimal, datetime)."""
    out: dict[str, Any] = {}
    for col in model.__table__.columns:
        if col.name not in row:
            continue
        val = row[col.name]
        if val is None:
            out[col.name] = None
            continue
        pytype = col.type.python_type
        if pytype is Decimal:
            out[col.name] = Decimal(str(val))
        elif pytype in (dt.datetime, dt.date):
            out[col.name] = dt.datetime.fromisoformat(val)
        else:
            out[col.name] = val
    return out


def import_bundle(session: Session, bundle: dict[str, Any], *, replace: bool = True) -> dict[str, int]:
    """Load a bundle. REPLACE mode wipes existing data first and preserves ids.

    Raises ValueError on unknown/newer schema_version.
    """
    version = bundle.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schema_version: {version!r} (expected {SCHEMA_VERSION})")
    tables = bundle.get("tables", {})

    if replace:
        # Delete children-first (reverse dependency order).
        for name, model in reversed(_ORDER):
            session.execute(delete(model))
        session.flush()

    counts: dict[str, int] = {}
    for name, model in _ORDER:
        rows = tables.get(name, [])
        for row in rows:
            session.add(model(**_coerce(model, row)))
        counts[name] = len(rows)
        session.flush()

    # Realign IDENTITY sequences to MAX(id) so future inserts don't collide.
    if replace:
        for name, model in _ORDER:
            session.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{model.__tablename__}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {model.__tablename__}), 1))"
                )
            )
    return counts


def import_json(session: Session, data: str, *, replace: bool = True) -> dict[str, int]:
    return import_bundle(session, json.loads(data), replace=replace)
