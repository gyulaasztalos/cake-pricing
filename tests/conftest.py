"""Shared pytest fixtures.

Integration + browser tests need a real Postgres (DATABASE_URL) — they are
skipped otherwise so plain unit runs still work. Browser tests additionally need
Playwright + Chromium; a live uvicorn server is started once per session.

Run everything:
    podman run -d --name cakepg -e POSTGRES_PASSWORD=devpass -e POSTGRES_USER=cake \\
        -e POSTGRES_DB=cake-pricing -p 55432:5432 postgres:18
    export DATABASE_URL=postgresql+psycopg://cake:devpass@localhost:55432/cake-pricing
    uv run alembic upgrade head
    uv run pytest
"""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

HAS_DB = bool(os.getenv("DATABASE_URL"))


# --- DB helpers --------------------------------------------------------------

def _reset_db() -> None:
    """Truncate all data and restore the seed groups (fast, keeps schema)."""
    from sqlalchemy import text

    from app.db import SessionLocal

    s = SessionLocal()
    try:
        s.execute(
            text(
                "TRUNCATE groups, components, component_prices, customers, offers, "
                "offer_components, recipes, recipe_items, stock_movements RESTART IDENTITY CASCADE"
            )
        )
        s.execute(
            text(
                "INSERT INTO groups (name, sort_order) VALUES "
                "('Alap', 10), ('Piskóta', 20), ('Töltelék', 30), "
                "('Burkolat', 40), ('Dekor', 50), ('Doboz', 60)"
            )
        )
        s.commit()
    finally:
        s.close()


@pytest.fixture
def clean_db():
    """Function-scoped: give the test an empty DB with only the seed groups."""
    if not HAS_DB:
        pytest.skip("requires DATABASE_URL")
    _reset_db()
    yield


def _seed_component(name: str, group_name: str, unit: str, ctype: str,
                    base_amount: str, base_price: str):
    from app.db import SessionLocal
    from app.models import Component, ComponentPrice, Group

    s = SessionLocal()
    try:
        gid = s.query(Group).filter_by(name=group_name).one().id
        c = Component(name=name, group_id=gid, unit=unit, type=ctype, active=True)
        s.add(c)
        s.flush()
        s.add(ComponentPrice(component_id=c.id, base_amount=Decimal(base_amount),
                             base_price=Decimal(base_price)))
        s.commit()
        return c.id
    finally:
        s.close()


@pytest.fixture
def seed_component():
    """Factory to create a priced component; returns its id."""
    return _seed_component


# --- live server + browser (session-scoped) ----------------------------------

def _free_port() -> int:
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def live_server() -> Iterator[str]:
    # Function-scoped: a pristine server per test so no pooled connection carries
    # a transaction/snapshot across the per-test DB reset (deterministic > fast
    # for a handful of browser tests).
    if not HAS_DB:
        pytest.skip("requires DATABASE_URL")
    import httpx

    port = _free_port()
    env = {**os.environ, "APP_ENV": "test"}
    # Launch uvicorn from the venv directly. Going through `uv run` spawns an
    # extra parent process; terminating it can orphan the real server and, more
    # importantly, its writes did not settle reliably in testing. The venv binary
    # is the actual server process we manage.
    uvicorn_bin = Path(sys.executable).parent / "uvicorn"
    proc = subprocess.Popen(  # noqa: S603
        [str(uvicorn_bin), "app.main:app", "--port", str(port)],
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    # Bypass any corporate proxy for localhost (trust_env=False), else the
    # health probe (and the browser) would route 127.0.0.1 through the proxy.
    client = httpx.Client(trust_env=False, timeout=0.5)
    try:
        for _ in range(100):
            if proc.poll() is not None:
                raise RuntimeError("uvicorn exited during startup")
            try:
                if client.get(f"{base}/healthz").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("uvicorn did not become ready")
        client.close()
        yield base
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        # --no-proxy-server: never route 127.0.0.1 through any proxy. (The old
        # proxy={"server": "direct://"} errors on hosts WITHOUT a proxy.)
        b = p.chromium.launch(args=["--no-proxy-server"])
        yield b
        b.close()


@pytest.fixture
def page(browser, clean_db, live_server):
    # clean_db before live_server: the server starts against a freshly reset DB.
    context = browser.new_context(base_url=live_server)
    pg = context.new_page()
    yield pg
    context.close()
