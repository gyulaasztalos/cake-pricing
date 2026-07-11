"""Application entrypoint.

Minimal skeleton for now: a health endpoint so the image builds, deploys and
passes k8s probes end-to-end. The real UI (grouped offer form, catalogs, etc.)
is layered on top of this in later tasks — see PLANNING.md.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app import __version__

app = FastAPI(title="cake-pricing", version=__version__)

# Expose Prometheus metrics at /metrics (scraped by the ServiceMonitor).
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)


@app.get("/healthz")
def healthz() -> JSONResponse:
    """Liveness/readiness probe target. No DB dependency (see /readyz for that)."""
    return JSONResponse({"status": "ok", "version": __version__})


@app.get("/readyz")
def readyz() -> JSONResponse:
    """Readiness including dependencies.

    DB connectivity check is added when the DB layer lands; for now the app is
    ready as soon as the process serves requests.
    """
    return JSONResponse({"status": "ready", "version": __version__})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Placeholder landing page (Hungarian). Replaced by the real UI later."""
    env = os.getenv("APP_ENV", "prod")
    return (
        "<!doctype html><html lang='hu'><head><meta charset='utf-8'>"
        "<title>Torta árazó</title></head><body>"
        f"<h1>Torta árazó</h1><p>cake-pricing v{__version__} ({env}) — készül. 🎂</p>"
        "</body></html>"
    )
