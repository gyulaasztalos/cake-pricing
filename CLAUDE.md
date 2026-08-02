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
- **9 tables** ([`app/models.py`](app/models.py) / [`schema.sql`](schema.sql)):
  `groups`, `components`, `component_prices` (append-only), `customers`,
  `offers`, `offer_components`, `recipes`/`recipe_items`, `stock_movements`
  (append-only), `price_sync_state` (singleton).
- Routers under `app/routers/` (one per domain area); services under
  `app/services/`; the external write path is
  [`app/routers/intake.py`](app/routers/intake.py) (`POST /api/intake/offers`,
  bearer token) — cake-order calls it to create external draft offers.
- **Daily price sync**: a CronJob runs
  [`app/jobs/price_sync.py`](app/jobs/price_sync.py) (`python -m
  app.jobs.price_sync`) — downloads the árfigyelő XLSX, updates component base
  prices via the temporal mechanism, e-mails a report, and records
  `price_sync_state.last_success_at` (a `/metrics` gauge for staleness alerts).
- **Calendar**: [`app/routers/calendar.py`](app/routers/calendar.py) — `/schedule`
  month view + the tokenized `/calendar/{token}/offers.ics` feed.
- **Recipes** (Receptek): [`app/routers/recipes.py`](app/routers/recipes.py) —
  `/recipes` reusable line sets. Note the router module is `recipes.py` but the
  Jinja dir is `app/templates/recipes/`; the ORM models are `Recipe`/`RecipeItem`
  ("template/Sablon" was the old UI name — never reintroduce it). "Save as recipe"
  from the offer form strips the **Alap** (base-cost) group.

## Conventions & invariants (don't break these)

- **Never store cost.** Query `v_offer_cost` / use `pricing.py`. `entry_date` is
  the immutable pricing anchor; external drafts stay `entry_date = NULL` until the
  chef first saves them (intake writes an explicit SQL `NULL`, not Python `None`).
- **Append-only tables** (`component_prices`, `stock_movements`): correct by
  inserting a new row (new window / correction movement), never update in place.
  A price change (manual or the sync job) closes the open window (`expiration_date
  = now`) and inserts a new one — see `change_price` / `price_sync._apply_price_change`.
- **Hungarian numbers**: amount/price inputs are `type=text inputmode=decimal`,
  parsed by `decimal_hu()` in `_helpers.py` (comma + spaces); the `amount` filter
  trims trailing zeros for display.
- **Machines use tokens, humans use Authentik**: the `.ics` feed is token-gated in
  the app AND its `/calendar/` IngressRoute rule skips Authentik. Any new
  machine-accessed endpoint carrying customer data must do the same.
- **`.venv/bin/…` for tools; `uv lock` after editing deps (incl. after a version
  bump — the lock records the package version).**
- **Release/version bump** (deploy is Helm-chart-based). Bump `pyproject.toml` +
  `app/__init__.py`, run `uv lock`, then bump `image.tag` in **both** values files:
  1. `../homelab-charts/charts/cake-pricing/values.yaml`
  2. `../ArgoCD/apps/cake-pricing/values.yaml`
  That single tag now drives **all three** workloads — Deployment, migrate Job, and
  price-sync CronJob — because the Jobs live in the chart (`jobs:` / `cronJobs:`,
  `common >= 0.6.0`), not as raw manifests. Verify with
  `helm template charts/cake-pricing | grep image:` (expect one tag everywhere).
  The migrate Job runs `alembic upgrade head` **before** the new app starts; if its
  image ever lagged the Deployment, a release with a migration would deploy an app
  whose DB lacks the new column (this bit us at 1.11.0 → `offers.paid` 500s, back
  when the Jobs were separate hardcoded manifests). A semver git tag builds/pushes
  the image. Run the full gate set before committing.
- **`see_other()` after every write** (commit-before-redirect); the intake API
  **commits before its 201** because cake-order marks its order forwarded on that
  ack.
- **HTMX dual-return**: list routes return full HTML normally, the `_rows`/section
  partial when `HX-Request` is set. Preserve that when adding filters.
- **Multi-select list filters** use the reusable `macros.multiselect` (a native
  `<details>` + checkboxes, no JS); the router param is `list[str] = Query([])`
  and empty = no filter (show all). The offers status filter additionally defaults
  to the *active* statuses on first load via a hidden `f=1` marker that tells
  first-load from an explicit empty selection — keep that marker when editing the
  offers filter form.
- **Both offers-list dropdowns use `macros.multiselect`** — status AND year. A
  native `<select>` popup cannot be styled, so mixing the two looked inconsistent.
  Each renders `data-filter="<name>"` for CSS/tests. Same `f=1` marker semantics:
  first load applies a default (active statuses / the current year), an explicit
  submission wins, and nothing ticked means "no filter", never an empty list.
- **Price-confidence badges** (`offers/_price_flags.html`, shared by the detail
  view and the edit form) distinguish `used_fallback` (amber `$` — priced from the
  earliest row because none covered `entry_date`) from `price_missing` (red `⚠` —
  no price rows at all, line is 0 Ft). Different ICON as well as colour, and both
  carry a `title`; never go back to a bare untranslated label.
- **The offers list sorts by `due_date`, not creation** — ascending (nearest
  deadline first) by default, reversed by the `desc` checkbox. `nullslast()` is
  applied in BOTH directions on purpose so undated offers never head the list;
  `Offer.id.desc()` is the stable tiebreak. `entry_date`/`request_date` still drive
  only the year filter + dropdown.
- **Profit % is DERIVED, never stored** — like cost. The offer form shows
  `final_price / calculated_price - 1`; only `app_settings.default_profit_pct`
  (Beállítások, a seeded singleton) is persisted, and it *only* prefills a
  not-yet-priced offer. The two footer fields are bound in `offer-form.js`:
  editing either recomputes the other, and a cost-base change follows whichever
  was edited last (initially the %). Never add an `offers.profit_pct` column.
- **Money/percent inputs are `type=text inputmode=decimal`** + `decimal_hu()`, so
  the Hungarian comma survives any browser locale — `type=number` silently
  discards "12,5". This covers final_price, paid, profit % and the amounts.
- **`app_settings` is a seeded singleton**: `tests/conftest._reset_db` restores it
  (UPSERT) rather than truncating, or a saved default leaks between tests.
- **Raw SQL in `stats.py`** is intentional; interpolate only module constants /
  the fixed `flavor`/`theme`/`sponge` identifiers, and always **bind** user values.
  Use `CAST(:year AS INTEGER)` (not `:year::int`, which breaks SQLAlchemy binds).
- **Prefer the server-rendered / native-HTML option; add JS only for genuine
  interactivity.** Check first whether a Jinja expression, CSS, or a native element
  does the job (`macros.multiselect` is a `<details>` + checkboxes with no JS). But
  *don't drop live behaviour to avoid JS*: values the chef watches while typing DO
  need a script — server-render them for the no-JS/first-paint case and enhance from
  there (see the "Ft / szelet" note: Jinja renders it, `offer-form.js` keeps it
  live). Inline `<script>`/`onclick` is permitted here (no strict CSP, unlike
  cake-order), but external files are still preferred.
- **If JS must re-implement a Python formatter, pin the parity with a test.**
  `offer-form.js` mirrors `templating.format_huf` (whole forint, U+00A0 separators)
  and carries its own `roundHalfToEven` because Python's `round()` is half-to-EVEN
  while JS `Math.round` is half-UP (12500.5 → 12500 vs 12501).
  `tests/test_huf_js_parity.py` executes the shipped JS under node and diffs it
  against Python — keep it green when touching either side.
- **Slice count** is `offers.portions` (Szelet) — set by intake OR the chef, always
  optional here (cake-order makes it mandatory for its per-slice cake types). It
  renders as `t('offers.portions_short')` → "12 szeletes" in the list/calendar, and
  with a final price yields `t('offers.per_portion')`. Per-slice money is always
  derived from **`final_price`, never `paid`**.
- **Choice fields that arrive from intake are FREE TEXT here.** cake-order owns the
  option list (slugs + hu/en/de labels in its `i18n.py`) and sends the **Hungarian
  display name**; this app stores it verbatim in a plain `Text` column, so the chef
  can also type anything. That is why `theme`/`flavor`/`sponge` are not enums and
  why stats can group them by value. Adding another such field = column + migration,
  `IntakeOffer` field, offer-form input, list/calendar display, and a `_top()` call
  in `stats.py` (mirror `sponge`, added 2026-07-26, for the full checklist).
- **i18n**: user-facing strings via `t()`; add to the Hungarian catalog.
- **CI parity** with cake-order (Postgres service + `alembic upgrade head`), plus
  Playwright Chromium.
- **Python 3.14**: parenless `except A, B:` (PEP 758) is valid; ruff removing the
  parens is correct.
