# cake-pricing — Architecture

Extended, self-contained documentation for humans and AI assistants who need to
add a feature or troubleshoot a bug. Pairs with [`README.md`](../README.md)
(what/how-to-run), [`CLAUDE.md`](../CLAUDE.md) (quick orientation), and
[`schema.sql`](../schema.sql) (annotated DDL). The `§` references in code
comments point to a **private** `PLANNING.md` that is not in this public repo;
everything needed to work in the code is captured here.

---

## 1. The big picture

cake-pricing is the **internal, staff-only** tool that turns cake ideas into
priced offers, and doubles as the catalog + inventory + customer + statistics
system. Unlike its public sibling cake-order, the database here is the **system
of record**, not a buffer.

The defining idea is **temporal pricing**: component prices change over time, and
an offer must always reflect the price that was valid on **its own pricing date**.
Cost is therefore **never stored** — it is computed on the fly from the price
history, so re-opening a two-year-old offer shows the same numbers it always did.

```
   cake-order (public) ──bearer token──▶  POST /api/intake/offers  ──▶  external DRAFT offer
                                                                        (no pricing date yet)
   Chef (browser) ──Authentik forward-auth──▶  cake-pricing UI  ──▶  PostgreSQL (CNPG)
                                                     │
                                                     └── SQL views compute cost/margin/stock
```

## 2. Code layout

```
app/
  main.py            FastAPI factory: no-store middleware, /healthz /readyz
                     /metrics, router includes, / → /offers redirect.
  config.py          Settings from env (locale, anonymize window, intake token…).
  db.py              Engine + SessionLocal + get_session() dependency.
  models.py          ORM mirror of schema.sql (8 tables).
  i18n.py            Hungarian catalog + t(key) (i18n-ready for more locales).
  templating.py      Jinja env + filters (huf, amount, date, datetime).
  routers/
    offers.py        Offer list/detail, create/edit form, live HTMX recalc,
                     templates→offer, delete.
    components.py    Component CRUD + price windows.
    groups.py        Component groups (sections + sort order).
    customers.py     Customer CRUD, history, anonymization.
    templates.py     Recipes (reusable line sets).
    inventory.py     Stock list, receive deliveries, corrections.
    stats.py         /stats dashboard (KPIs + charts).
    settings.py      JSON export/import (portability).
    intake.py        POST /api/intake/offers — the one external write path.
    _helpers.py      see_other() (commit-before-redirect), get_or_404().
    calendar.py      /naptar month view + the tokenized .ics feed.
  services/
    pricing.py       Temporal price selection + line-cost math (mirrors views).
    offers.py        Group view-models for the form; save lines + resync stock.
    stock.py         Stock-movement ledger helpers; per-offer consumption sync.
    stats.py         Aggregate read-model + server-rendered SVG bar charts.
    calendar.py      .ics generation + month bucketing (Europe/Budapest).
    price_sync.py    XLSX download/parse + temporal price reconciliation.
    mailer.py        SMTP send of the price-change report e-mail.
    portability.py   Export/import serialization.
  jobs/price_sync.py Daily CronJob entrypoint (python -m app.jobs.price_sync).
  metrics.py         Custom /metrics gauge: last price-sync success (per scrape).
schema.sql           Annotated DDL: tables, constraints, and the cost/stock views.
migrations/          Alembic (0001 baseline, 0002 intake, 0003 price sync).
tests/               pytest; DB + Playwright browser tests (skip without deps).
```

## 3. Data model

Nine tables (see [`app/models.py`](../app/models.py) / [`schema.sql`](../schema.sql)).
Conventions: mutable tables carry `entry_date` + auto-updated `update_date`;
append-only tables carry `entry_date` only. Money is `Numeric(12,2)` HUF;
quantities/multipliers `Numeric(12,3)`.

| Table | Role | Notes |
|-------|------|-------|
| `groups` | Offer/catalog sections | `sort_order` drives display order |
| `components` | Priced building blocks | `type ∈ {ingredient, service, stock_item}`; optional `product_id` (Termék azonosító) + `price_missing_at` (sync warning) |
| `component_prices` | **Temporal, append-only** price history | half-open `[effective_date, expiration_date)`; a GiST **EXCLUDE** constraint forbids overlapping windows per component |
| `customers` | People/orgs | `anonymized_at` scrubs identity after the retention window |
| `offers` | The core entity | see lifecycle below |
| `offer_components` | Offer line items | `(component_id, amount)` |
| `recipes` / `recipe_items` | Templates | reusable line sets to seed offers |
| `stock_movements` | **Append-only** inventory ledger | `reason ∈ {delivery, order, correction}`; `order` movements carry `offer_id` |
| `price_sync_state` | Singleton (id=1) | `last_success_at` of the daily price-sync job (read by `/metrics`) |

### Offers: lifecycle & the two provenances
- **Status**: `draft → sent → accepted → rejected → done` (CHECK-constrained).
- **`source`**: `internal` (built by the chef) or `external` (came from cake-order).
- **`entry_date` is the pricing reference date** and is *immutable once set*. For
  external drafts it is **NULL** until the chef first saves/prices the offer —
  `request_date` records when the customer submitted. (This NULL-until-priced rule
  is why the intake API writes an explicit SQL `NULL`, see §5.)

## 4. The pricing model (the heart of the app)

Cost is computed, never stored. Two SQL views ([`schema.sql`](../schema.sql)) are
the source of truth, and [`app/services/pricing.py`](../app/services/pricing.py)
is their Python mirror for live recalc on the not-yet-saved form.

- **`v_offer_line_cost`** — for each offer line, a `LATERAL` join picks exactly
  one `component_prices` row: the window that **covers the offer's `entry_date`**,
  else the **earliest** price as a fallback (so a missing window never silently
  drops the line). `line_price = ROUND(amount / base_amount * base_price, 0)`
  (whole HUF).
- **`v_offer_cost`** — `calculated_price = SUM(line_price)` per offer, plus
  `final_price` (the chef's quote) and `price_delta = final − calculated`.
- **`v_component_stock`** — `SUM(qty_delta)` per component.

**Why "temporal" matters:** the price chosen depends on the offer's `entry_date`,
not `now()`. Editing an old offer's lines re-sums *its* historical prices; it does
**not** re-price at today's rates. `save_offer_lines()` never touches
`entry_date`.

> When you touch pricing, keep the Python (`pricing.py`) and SQL (views) in step —
> tests assert they agree (e.g. `test_integration.py`).

## 5. Intake API — the one external write path

[`app/routers/intake.py`](../app/routers/intake.py), `POST /api/intake/offers`,
is the *only* way the outside world writes here. It is deliberately narrow:

- **Auth**: a bearer token (`INTAKE_TOKEN`), compared with `hmac.compare_digest`
  on bytes. Empty token → `503` (disabled). Reachable both via the ClusterIP
  service (cake-order calls it directly, bypassing the ingress) and via the
  Authentik ingress (forward-auth first); the token is required either way.
- **Effect**: find-or-create a **non-anonymized** customer matched on the e-mail
  (anchored match to avoid `ann@x` attaching to `joann@x`), then insert an
  **external draft** offer with `request_date` and an explicit `entry_date = NULL`
  (a plain Python `None` would let the column default `now()` price the draft).
- **Durability**: it **commits before returning 201**, because cake-order marks
  its order `forwarded` on that ack — the same commit-before-acknowledge rule as
  `see_other()`.

## 6. Inventory

[`app/services/stock.py`](../app/services/stock.py). Current stock is
`SUM(qty_delta)`. Saving an offer calls `sync_offer_consumption()`, which is
idempotent: it deletes that offer's existing `order` movements and re-creates one
negative movement per **stock-item** line. Deleting an offer removes its movements
via FK cascade. Zero stock is **warning-only** — never blocks a save.

## 7. Statistics (`/stats`)

[`app/services/stats.py`](../app/services/stats.py) +
[`app/routers/stats.py`](../app/routers/stats.py). A pure read-model over `offers`
+ `v_offer_cost`. One `year` param drives the scope: **none → all-time KPIs + a
per-year series**; **`YYYY` → that year + a per-month series**. The "created"
moment is `COALESCE(entry_date, request_date)`, bucketed in Europe/Budapest.
Charts are **server-rendered SVG** (no JS/deps); every dynamic label is escaped.
Only aggregates are shown, so anonymized customers keep contributing to totals
without being identifiable.

> The raw-SQL is intentional (grouping + timezone extraction). Interpolated
> fragments are module constants or the fixed identifiers `flavor`/`theme`; the
> only user value (`year`) is always a **bound param** — hence the scoped
> `S608` ignore in `pyproject.toml`. Note `:year::int` breaks SQLAlchemy's bind
> parsing (it protects `::` casts), so the code uses `CAST(:year AS INTEGER)`.

## 8. UI conventions

- **HTMX partials**: list pages render full HTML on a normal GET and just the
  `_rows`/section partial when `HX-Request` is present. Keep that dual-return
  shape when adding list filters.
- **`see_other()`** (commit-before-redirect) after every create/edit/delete —
  `get_session()` commits in teardown *after* the response, so a slow host could
  otherwise redirect to a list that reads stale data.
- **Money/format filters** live in `templating.py` (`| huf`, `| amount`, `| date`).
- This is an **internal app**, so (unlike cake-order) inline `<script>`/`onclick`
  is allowed — there is no strict CSP here.
- **i18n**: all strings via `t()`; Hungarian is the only catalog today but the
  layer is ready for more.

## 9. Calendar (Naptár)

[`services/calendar.py`](../app/services/calendar.py) +
[`routers/calendar.py`](../app/routers/calendar.py). Two deliberately separate
paths (PLANNING §Calendar):
- **`/naptar`** — the human month view, behind Authentik. Row-per-day, every day
  of the month; click a day → new offer with that `due_date` pre-filled.
- **`/calendar/{token}/offers.ics`** — the machine feed. Calendar apps can't do
  Authentik forward-auth, so the IngressRoute gives `/calendar/` its own rule
  with **default-headers only**; the path carries an unguessable `CALENDAR_TOKEN`
  the app verifies (empty token ⇒ 404). One all-day VEVENT per offer
  (UID `offer-<id>`), RFC 5545 escaping/CRLF/**octet-based folding** (Hungarian
  accents are 2 bytes). Deadlines are bucketed by their **Europe/Budapest** date
  (stored UTC). Rejected offers are excluded. Subscription URL lives on Beállítások.

## 10. Daily price sync (automatic price update)

[`services/price_sync.py`](../app/services/price_sync.py) +
[`jobs/price_sync.py`](../app/jobs/price_sync.py). A k8s **CronJob** (08:00
Europe/Budapest, `timeZone` set so no DST maths) runs `python -m
app.jobs.price_sync`:
1. download the *árfigyelő* XLSX (`PRICE_SYNC_URL`);
2. parse **column A** (Termék azonosító, zero-padded string) → **column I**
   ("Maximum ár", Hungarian comma). A product id appears once per store chain, so
   several rows are **averaged** to whole forint (owner's choice);
3. for every component with a `product_id` (**exact** string match), if the price
   differs, apply a **temporal price change** (close the open `component_prices`
   row, insert a new one — same mechanism as the manual endpoint); ids not found
   get `price_missing_at` (⚠ in the UI);
4. commit, then **e-mail a report** to `ORDER_INBOX` when there were changes *or*
   not-found ids (reuses cake-order's iCloud SMTP creds via ESO);
5. `mark_success()` bumps `price_sync_state.last_success_at`.

Any failure raises → the Job fails → **AlertManager** (`price-sync-failed` on
`kube_job_status_failed`). The app exposes
`cake_pricing_price_sync_last_success_timestamp_seconds` on `/metrics`
([`metrics.py`](../app/metrics.py), read from the DB per scrape); `price-sync-stale`
fires when it's > 25h old. The migration seeds `last_success_at = now()` so the
staleness clock starts at deploy (no false alarm, but a never-running job still
alerts).

## 11. Deployment topology

- **Image**: multi-arch `python:3.14-slim`, `uvicorn app.main:app` on `:8000`.
- **k3s / ArgoCD** (`apps/cake-pricing/install/`): Deployment, Service, Traefik
  IngressRoute **behind Authentik forward-auth** (internal host, no public
  tunnel), plus the price-sync **CronJob** and its SMTP ExternalSecret. `readyz`
  gates traffic on DB reachability. (No NetworkPolicy — cake-pricing is internal,
  not a DMZ, so the job reaches the CDN/SMTP freely.)
- **Postgres**: a shared CloudNativePG cluster with a managed role; creds via
  External Secrets Operator (1Password). Alembic migrations run on deploy.
- **CI parity** with cake-order (Postgres service + `alembic upgrade head`), plus
  Playwright Chromium for the browser tests.

## 12. Gotchas & conventions

- **Python 3.14 target.** `except A, B:` without parens is valid (PEP 758); ruff
  removing the parens is correct.
- **Cost is never a column.** If you need cost anywhere, query `v_offer_cost` /
  use `pricing.py` — do not add a stored cost.
- **`entry_date` is immutable** and is the pricing anchor; external drafts stay
  NULL-priced until first save.
- **Append-only tables** (`component_prices`, `stock_movements`) are never updated
  in place — correct by adding a new row (a new price window, a correction
  movement). The price-sync job uses the SAME temporal mechanism.
- **Hungarian numbers** — amount/price inputs are `type=text inputmode=decimal`
  and parsed by `decimal_hu()` in `_helpers.py` (comma + space thousands);
  display trims trailing zeros via the `amount` filter. Component base
  amount/price go through `_price_decimal` (422 on garbage/negative).
- **Machines use tokens, humans use Authentik** — the `.ics` feed (and any future
  machine endpoint) is token-gated in the app AND given an Authentik-free
  IngressRoute rule; never expose customer data on such a path without the token.
- **Testing**: DB tests skip without `DATABASE_URL`; browser tests need Chromium
  and are `-k "not browser"`. Under load use `.venv/bin/python -m pytest`; if
  mypy's cache errors, `mypy --no-incremental --cache-dir=/dev/null`.
