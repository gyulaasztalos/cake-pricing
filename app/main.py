"""Application entrypoint / factory.

Wires static assets, the page routers, and the ops endpoints (/healthz, /readyz,
/metrics). The real UI lives in the routers under app/routers/.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from app import __version__
from app.routers import (
    components,
    customers,
    groups,
    intake,
    inventory,
    offers,
    settings,
    templates,
)

app = FastAPI(title="cake-pricing", version=__version__)

Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

for router in (offers, customers, components, groups, templates, inventory, settings, intake):
    app.include_router(router.router)


@app.get("/healthz")
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "version": __version__})


@app.get("/readyz")
def readyz() -> JSONResponse:
    return JSONResponse({"status": "ready", "version": __version__})


@app.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/offers")
