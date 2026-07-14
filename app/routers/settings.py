"""Beállítások (Settings) — DB export/import (versioned JSON bundle, §5.2)."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import portability
from app.templating import templates

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return templates.TemplateResponse(request, "settings/index.html", {"active_nav": "settings"})


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
    return RedirectResponse(url="/settings", status_code=303)
