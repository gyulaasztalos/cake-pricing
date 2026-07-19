# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Internal, staff-only **cake offer-pricing + catalog + inventory + stats** web app
(FastAPI + SQLAlchemy + Jinja/HTMX + Pico.css, Python 3.14, `uv`). Sibling of the
public **cake-order** app; deployed to a k3s HomeLab via the **ArgoCD** repo,
reached behind **Authentik** forward-auth (not public). Full detail:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md); overview: [`README.md`](README.md);
annotated DDL: [`schema.sql`](schema.sql). Spec lives in a **private**
`PLANNING.md` (the `§` refs) — not in this repo.

## Commands

```bash
uv sync
# DB is required for most of the app and its tests:
podman run -d --name cakepricingpg -e POSTGRES_PASSWORD=devpass -e POSTGRES_USER=cake \
  -e POSTGRES_DB=cake-pricing -p 55434:5432 postgres:18
export DATABASE_URL="postgresql+psycopg://cake:devpass@localhost:55434/cake-pricing"
uv run alembic upgrade head

uv run uvicorn app.main:app --reload      # :8000 → /offers
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                             # single: uv run pytest tests/test_stats.py -q
uv run pytest -k "not browser"            # skip Playwright tests
uv run playwright install --with-deps chromium   # once, for browser tests
```

If `uv run` is flaky under pyenv, use `.venv/bin/…`. If mypy's incremental cache
throws a disk error, run `mypy --no-incremental --cache-dir=/dev/null`. Under
heavy machine load, test/mypy startup can be very slow — wait rather than assume a
hang.

## Architecture in one screen

- **Temporal pricing is the core idea.** Component prices have non-overlapping
  time windows; an offer is priced with the price valid on **its own
  `entry_date`**. **Cost is never stored** — it's computed by SQL views
  (`v_offer_line_cost`, `v_offer_cost`) mirrored in
  [`app/services/pricing.py`](app/services/pricing.py).
- **8 tables** ([`app/models.py`](app/models.py) / [`schema.sql`](schema.sql)):
  `groups`, `components`, `component_prices` (append-only), `customers`,
  `offers`, `offer_components`, `recipes`/`recipe_items`, `stock_movements`
  (append-only).
- Routers under `app/routers/` (one per domain area); services under
  `app/services/`; the external write path is
  [`app/routers/intake.py`](app/routers/intake.py) (`POST /api/intake/offers`,
  bearer token) — cake-order calls it to create external draft offers.

## Conventions & invariants (don't break these)

- **Never store cost.** Query `v_offer_cost` / use `pricing.py`. `entry_date` is
  the immutable pricing anchor; external drafts stay `entry_date = NULL` until the
  chef first saves them (intake writes an explicit SQL `NULL`, not Python `None`).
- **Append-only tables** (`component_prices`, `stock_movements`): correct by
  inserting a new row (new window / correction movement), never update in place.
- **`see_other()` after every write** (commit-before-redirect); the intake API
  **commits before its 201** because cake-order marks its order forwarded on that
  ack.
- **HTMX dual-return**: list routes return full HTML normally, the `_rows`/section
  partial when `HX-Request` is set. Preserve that when adding filters.
- **Raw SQL in `stats.py`** is intentional; interpolate only module constants /
  the fixed `flavor`/`theme` identifiers, and always **bind** user values. Use
  `CAST(:year AS INTEGER)` (not `:year::int`, which breaks SQLAlchemy binds).
- **Internal app**: inline `<script>`/`onclick` is fine (no strict CSP), unlike
  cake-order.
- **i18n**: user-facing strings via `t()`; add to the Hungarian catalog.
- **CI parity** with cake-order (Postgres service + `alembic upgrade head`), plus
  Playwright Chromium.
- **Python 3.14**: parenless `except A, B:` (PEP 758) is valid; ruff removing the
  parens is correct.
