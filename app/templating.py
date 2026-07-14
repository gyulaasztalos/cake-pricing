"""Jinja environment + shared template globals/filters."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings
from app.i18n import t

TEMPLATES_DIR = Path(__file__).parent / "templates"


def format_huf(value: Decimal | int | float | None) -> str:
    """1234567 -> '1 234 567 Ft' (Hungarian: space thousands separator)."""
    if value is None:
        return "—"
    n = int(round(float(value)))
    return f"{n:,}".replace(",", " ") + " Ft"


def format_amount(value: Decimal | int | float | None) -> str:
    """Trim trailing zeros: 1000.000 -> '1000', 2.800 -> '2.8'."""
    if value is None:
        return ""
    d = Decimal(str(value)).normalize()
    # avoid scientific notation for integers like 1E+3
    return format(d, "f")


def format_date(value: dt.datetime | dt.date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d")


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["t"] = t
templates.env.globals["locale"] = settings.default_locale
templates.env.globals["app_env"] = settings.app_env
templates.env.filters["huf"] = format_huf
templates.env.filters["amount"] = format_amount
templates.env.filters["date"] = format_date
