"""Jinja environment + shared template globals/filters."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app import __version__
from app.config import settings
from app.i18n import t

TEMPLATES_DIR = Path(__file__).parent / "templates"

_LOCAL_TZ: ZoneInfo | None
try:
    _LOCAL_TZ = ZoneInfo("Europe/Budapest")
except ZoneInfoNotFoundError:  # pragma: no cover - fallback if tzdata missing
    _LOCAL_TZ = None


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


def _to_local(value: dt.datetime) -> dt.datetime:
    if _LOCAL_TZ is None or value.tzinfo is None:
        return value
    return value.astimezone(_LOCAL_TZ)


def format_date(value: dt.datetime | dt.date | None) -> str:
    """Hungarian standard date: YYYY-MM-DD."""
    if value is None:
        return "—"
    if isinstance(value, dt.datetime):
        value = _to_local(value)
    return value.strftime("%Y-%m-%d")


def format_datetime(value: dt.datetime | None) -> str:
    """Hungarian standard timestamp: YYYY-MM-DD HH24:MI:SS (local time)."""
    if value is None:
        return "—"
    return _to_local(value).strftime("%Y-%m-%d %H:%M:%S")


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["t"] = t
templates.env.globals["locale"] = settings.default_locale
templates.env.globals["app_env"] = settings.app_env
templates.env.globals["version"] = __version__
templates.env.filters["huf"] = format_huf
templates.env.filters["amount"] = format_amount
templates.env.filters["date"] = format_date
templates.env.filters["datetime"] = format_datetime

# Separate env for e-mail bodies (autoescape HTML, but NOT the .txt alternative).
email_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
)
email_env.globals["t"] = t
email_env.filters["huf"] = format_huf
email_env.filters["amount"] = format_amount
email_env.filters["datetime"] = format_datetime
