"""Application entrypoint / factory.

Wires static assets, the page routers, and the ops endpoints (/healthz, /readyz,
/metrics). The real UI lives in the routers under app/routers/.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from prometheus_fastapi_instrumentator import Instrumentator

from app import __version__, metrics
from app.routers import (
    calendar,
    components,
    customers,
    groups,
    intake,
    inventory,
    offers,
    recipes,
    settings,
    stats,
)

app = FastAPI(title="cake-pricing", version=__version__)


@app.middleware("http")
async def no_store_dynamic(request: Request, call_next):
    """Dynamic pages must never be cached — otherwise a post-create/edit redirect
    can render a stale list from the browser cache. Static assets (/static) keep
    their default caching."""
    response = await call_next(request)
    if not request.url.path.startswith("/static"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
# Custom gauge: last successful price-sync run (read from the DB per scrape).
metrics.register()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

for router in (
    offers,
    calendar,
    customers,
    components,
    groups,
    recipes,
    inventory,
    settings,
    stats,
    intake,
):
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
