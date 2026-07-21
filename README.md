# cake-pricing

Internal **offer-pricing & catalog webapp** for the one-person pastry business
*Anita Tortái* (a *cukrász manufaktúra* in Szentendre). It replaces a
hand-maintained spreadsheet with a proper app: a grouped offer form, a component
catalog with **temporal pricing**, customer history, box/plate **inventory**, and
an offer **statistics** dashboard.

It is the private, staff-side counterpart to the public
**[cake-order](https://github.com/gyulaasztalos/cake-order)** app, which pushes
verified customer requests here as draft offers via a narrow intake API.

> **Design & requirements** live in a **private** `PLANNING.md` (not in this
> public repo). This README plus [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
> are the public, self-contained documentation. AI assistants: start with
> [`CLAUDE.md`](CLAUDE.md).

## What it does

- **Offers** — build a priced offer by picking components into groups (Alap,
  Piskóta, Töltelék, …); live cost recalc via HTMX; a final ("quoted") price is
  recorded alongside the **calculated** cost so margin is visible. Statuses:
  draft → sent → accepted → rejected → done.
- **Components & temporal prices** — each component (ingredient / service /
  stock_item) has an append-only price history with non-overlapping validity
  windows. An offer is always priced with the price **effective on its pricing
  date**, so re-opening an old offer never re-prices it.
- **Templates (recipes)** — reusable line sets to seed a new offer quickly.
- **Customers** — history and notes; auto-anonymization after a retention window.
- **Inventory** — a stock-movement ledger; stock-item lines on an offer consume
  stock automatically; deliveries/corrections adjust it. Warning-only at zero.
- **Statistics** (`/stats`) — KPIs (win rate, revenue, calculated cost, margin,
  average offer, new customers) with server-rendered SVG charts; all-time
  (yearly) or a single year (monthly). Aggregate-only, so anonymized customers
  are respected.
- **Calendar** (`/naptar`) — a month view of offer deadlines (rejected excluded),
  click a day to start a new offer for it. A published, tokenized `.ics` feed
  lets the chef subscribe in Apple Calendar (subscription URL on Beállítások).
- **Automatic price update** — a daily CronJob downloads the *árfigyelő* price
  file and updates the base price of any component tagged with a *Termék
  azonosító*; e-mails a change report, warns in the UI on ids it can't find, and
  exposes a last-success gauge on `/metrics` for staleness alerting.
- **Intake API** (`/api/intake/offers`) — the single token-authed write path used
  by cake-order to create an external draft offer.
- **Portability** — full JSON export/import of the database (settings page).

Amounts and prices accept the Hungarian **decimal comma** (`2,5`) in any browser
locale; edit forms return to the page you came from on Save/Cancel.

## Stack

FastAPI · SQLAlchemy 2.0 · Alembic · Jinja + HTMX · Pico.css · PostgreSQL ·
Python 3.14. Packaged with **uv**, built as a multi-arch (amd64 + arm64) Docker
image, deployed to a k3s HomeLab via **ArgoCD** GitOps, and reached internally
behind **Authentik** forward-auth (no public exposure).

## Local development

Requires [uv](https://docs.astral.sh/uv/) and PostgreSQL (the DB is central here —
temporal pricing uses SQL views and a GiST exclusion constraint).

```bash
uv sync                                   # create .venv from uv.lock

podman run -d --name cakepricingpg -e POSTGRES_PASSWORD=devpass -e POSTGRES_USER=cake \
  -e POSTGRES_DB=cake-pricing -p 55434:5432 postgres:18
export DATABASE_URL="postgresql+psycopg://cake:devpass@localhost:55434/cake-pricing"
uv run alembic upgrade head

uv run uvicorn app.main:app --reload      # http://localhost:8000 (redirects to /offers)

# quality gates (what CI runs)
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                             # a single test: uv run pytest tests/test_stats.py -q
```

Browser (Playwright) tests need Chromium: `uv run playwright install --with-deps
chromium`. They are deselected with `-k "not browser"`.

> If `uv run` is flaky under a pyenv shell, call venv binaries directly
> (`.venv/bin/python -m pytest`). If mypy's incremental cache hits a disk error,
> run `mypy --no-incremental --cache-dir=/dev/null`.

If you change dependencies in `pyproject.toml`, regenerate and commit the lock:
`uv lock`.

## Docker

```bash
docker build -t cake-pricing:dev .
docker run --rm -p 8000:8000 cake-pricing:dev
```

## Releases (CI/CD)

Every push/PR runs the workflow: `ruff check` + `ruff format --check`, `mypy`,
`alembic upgrade head` against a Postgres service, `pytest` (incl. Playwright
Chromium), `bandit`, `pip-audit`. Pushing a semver tag builds and pushes a
multi-arch image to Docker Hub (`asztalosgyula/cake-pricing`) and GHCR:

```bash
git tag v1.4.0 && git push origin v1.4.0
```

Renovate in the ArgoCD repo then bumps the deployed image tag → ArgoCD syncs it.

## Route map

UI (HTML, HTMX partials): `/offers`, `/naptar`, `/customers`, `/components`,
`/templates`, `/inventory`, `/groups`, `/stats`, `/settings` (export/import).
Ops: `/healthz`, `/readyz`, `/metrics`. Machine (tokenized, Authentik-skipped):
`GET /calendar/{token}/offers.ics`. API: `POST /api/intake/offers` (bearer token).

The daily price-sync CronJob runs `python -m app.jobs.price_sync` (not an HTTP
route) — see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Configuration

Env-driven ([`app/config.py`](app/config.py)): `APP_LOCALE`,
`ANONYMIZE_AFTER_YEARS`, `MASS_VOLUME_STEP`, `INTAKE_TOKEN` (empty disables the
intake API), `CALENDAR_TOKEN` (empty disables the `.ics` feed → 404), the
`SMTP_*` group + `MAIL_FROM`/`ORDER_INBOX` (price-report e-mail), and
`PRICE_SYNC_URL`. `DATABASE_URL` is required.

## Where to read next

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the pricing model, data model,
  offer lifecycle, intake, inventory, and deployment.
- [`CLAUDE.md`](CLAUDE.md) — orientation for AI assistants.
- [`schema.sql`](schema.sql) — annotated DDL mirror of the ORM + the cost views.
