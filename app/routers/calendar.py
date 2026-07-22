"""Naptár — offer deadlines as a month view and as a published .ics feed.

Two deliberately separate paths (PLANNING §Calendar):

  * ``/schedule``                    — the human month view, behind Authentik
                                       like the rest of the UI.
  * ``/calendar/{token}/offers.ics`` — the machine feed. Calendar apps cannot do
                                       Authentik forward-auth, so this prefix
                                       skips it at the ingress and is guarded by
                                       an unguessable token in the path instead
                                       ("machines use tokens, humans use
                                       Authentik").

The feed exposes customer names and prices, so a missing/incorrect token is a
plain 404 — never a hint that the path exists. Subscription instructions live on
the Beállítások page, not here.
"""

from __future__ import annotations

import datetime as dt
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_session
from app.services import calendar as calendar_svc
from app.templating import templates

router = APIRouter()

FEED_FILENAME = "offers.ics"

# Hungarian weekday abbreviations, Monday-first (matches date.weekday()).
WEEKDAY_SHORT = ("H", "K", "Sze", "Cs", "P", "Szo", "V")
MONTH_NAMES = (
    "január",
    "február",
    "március",
    "április",
    "május",
    "június",
    "július",
    "augusztus",
    "szeptember",
    "október",
    "november",
    "december",
)


def feed_path(token: str) -> str:
    return f"/calendar/{token}/{FEED_FILENAME}"


def feed_url(request: Request) -> str:
    """Absolute feed URL, for pasting into Apple Calendar. Empty when disabled."""
    if not settings.calendar_token:
        return ""
    return f"{request.base_url}".rstrip("/") + feed_path(settings.calendar_token)


def _parse_month(ym: str) -> tuple[int, int]:
    """`YYYY-MM` → (year, month); falls back to the current local month."""
    try:
        year_str, month_str = ym.split("-", 1)
        year, month = int(year_str), int(month_str)
        if 1 <= month <= 12 and 1970 <= year <= 9999:
            return year, month
    except ValueError:
        pass
    today = calendar_svc.local_today()
    return today.year, today.month


@router.get("/schedule", response_class=HTMLResponse)
def calendar_page(request: Request, ym: str = "", session: Session = Depends(get_session)):
    year, month = _parse_month(ym)
    prev_year, prev_month = calendar_svc.shift_month(year, month, -1)
    next_year, next_month = calendar_svc.shift_month(year, month, 1)
    today = calendar_svc.local_today()
    ctx = {
        "active_nav": "calendar",
        "days": calendar_svc.month_days(session, year, month),
        "year": year,
        "month": month,
        "month_label": f"{year}. {MONTH_NAMES[month - 1]}",
        "prev_ym": f"{prev_year:04d}-{prev_month:02d}",
        "next_ym": f"{next_year:04d}-{next_month:02d}",
        "today_ym": f"{today.year:04d}-{today.month:02d}",
        "is_current_month": (year, month) == (today.year, today.month),
        "weekday_short": WEEKDAY_SHORT,
    }
    return templates.TemplateResponse(request, "calendar/index.html", ctx)


@router.get("/calendar/{token}/" + FEED_FILENAME, include_in_schema=False)
def calendar_feed(token: str, session: Session = Depends(get_session)) -> Response:
    configured = settings.calendar_token
    # Constant-time compare on bytes; an empty configured token disables the feed.
    if not configured or not hmac.compare_digest(token.encode(), configured.encode()):
        raise HTTPException(status_code=404, detail="Not Found")
    body = calendar_svc.build_ics(session, now=dt.datetime.now(dt.UTC))
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{FEED_FILENAME}"',
            # Subscriptions poll; let clients revalidate rather than cache hard.
            "Cache-Control": "no-store",
        },
    )
