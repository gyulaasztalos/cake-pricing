"""Statisztika — offer KPIs and charts, all-time (yearly) or per-year (monthly)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_session
from app.services import stats as stats_svc
from app.templating import templates

router = APIRouter()


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, year: str = "", session: Session = Depends(get_session)):
    yr = int(year) if year.strip().isdigit() else None
    data = stats_svc.collect(session, yr)

    # Chart view models: revenue per bucket, and offers-per-bucket with the "won"
    # portion highlighted. Monthly buckets get Hungarian month labels.
    if data.series_kind == "month":
        labels = stats_svc.month_labels(data.series)
    else:
        labels = [p.label for p in data.series]
    revenue_chart = stats_svc.bar_chart(labels, [float(p.revenue) for p in data.series], money=True)
    offers_chart = stats_svc.bar_chart(
        labels,
        [float(p.offers) for p in data.series],
        highlights=[float(p.won) for p in data.series],
    )

    ctx = {
        "active_nav": "stats",
        "s": data,
        "revenue_chart": revenue_chart,
        "offers_chart": offers_chart,
    }
    return templates.TemplateResponse(request, "stats/index.html", ctx)
