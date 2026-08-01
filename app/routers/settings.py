"""Beállítások (Settings) — DB export/import (versioned JSON bundle, §5.2)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.routers import calendar as calendar_router
from app.routers._helpers import (
    decimal_hu,
    default_profit_pct,
    see_other,
    set_default_profit_pct,
)
from app.services import portability
from app.templating import templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = Depends(get_session)):
    # The .ics subscription URL lives here (not on the Naptár page) — it is a
    # one-time setup step, and the URL embeds the secret feed token.
    ctx = {
        "active_nav": "settings",
        "calendar_feed_url": calendar_router.feed_url(request),
        "default_profit_pct": default_profit_pct(session),
    }
    return templates.TemplateResponse(request, "settings/index.html", ctx)


@router.post("/settings/profit")
def save_default_profit(
    default_profit_pct: str = Form(""),
    session: Session = Depends(get_session),
):
    """Set the profit % prefilled on NEW offers. Existing offers are untouched —
    each one stores its own profit_pct, so this only affects the next new offer."""
    value = decimal_hu(default_profit_pct)
    if value is not None:
        set_default_profit_pct(session, value)
    return see_other(session, "/settings")


@router.get("/settings/export")
def export_data(session: Session = Depends(get_session)):
    now = dt.datetime.now(dt.UTC)
    payload = portability.export_json(session, exported_at=now.isoformat())
    filename = f"cake-pricing-export-{now:%Y%m%d-%H%M%S}.json"
    return Response(
        content=payload,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/settings/import")
async def import_data(file: UploadFile = File(...), session: Session = Depends(get_session)):
    raw = (await file.read()).decode("utf-8")
    portability.import_json(session, raw, replace=True)
    return see_other(session, "/settings")
