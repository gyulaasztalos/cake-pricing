-- ============================================================================
-- cake-pricing — PostgreSQL schema (DDL)
-- ============================================================================
-- Source of truth: PLANNING.md §3. This file is the design-task DDL draft; the
-- runtime app owns migrations via Alembic (this is the target end-state).
--
-- Conventions (PLANNING.md §3.0):
--   * Every mutable table: id (PK), entry_date (row created), update_date (row
--     last modified), plus name where meaningful.
--   * Append-only tables (component_prices, stock_movements): entry_date only,
--     NO update_date — rows are never modified in place.
--   * Money: NUMERIC(12,2) HUF (Forint has no practical subunit).
--     Quantities/multipliers: NUMERIC(12,3) (fractional grams, e.g. 2.8).
--   * All timestamps TIMESTAMPTZ. UTF-8 database (see createdb note below).
--   * Temporal prices use half-open [effective_date, expiration_date) intervals.
--
-- DB creation (done via CNPG Database CRD in prod; for reference):
--   CREATE DATABASE "cake-pricing" ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'
--                                  TEMPLATE template0;
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS btree_gist;   -- needed for the EXCLUDE constraint below

-- ---------------------------------------------------------------------------
-- GROUPS — component categories; drive UI sectioning of the offer form (§3.1).
-- UI-managed: create + edit name, NO delete.
-- ---------------------------------------------------------------------------
CREATE TABLE groups (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT        NOT NULL UNIQUE,
    sort_order   INTEGER     NOT NULL DEFAULT 0,   -- display order of sections
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- COMPONENTS — ingredients, boxes/plates (stock), and services (labor/overhead).
-- active=false hides it from the picker but never breaks past offers.
-- ---------------------------------------------------------------------------
CREATE TABLE components (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT        NOT NULL,             -- component_name (UTF-8, Hungarian)
    group_id     BIGINT      NOT NULL REFERENCES groups(id),
    unit         TEXT        NOT NULL DEFAULT 'db',-- g, ml, db (pcs), …
    type         TEXT        NOT NULL DEFAULT 'ingredient'
                 CHECK (type IN ('ingredient', 'service', 'stock_item')),
    active       BOOLEAN     NOT NULL DEFAULT true,
    notes        TEXT,
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_components_group  ON components(group_id);
CREATE INDEX idx_components_active ON components(active) WHERE active;

-- ---------------------------------------------------------------------------
-- COMPONENT_PRICES — temporal price history, APPEND-ONLY (§3.4).
-- Half-open [effective_date, expiration_date); expiration_date NULL = current.
-- A price change: set old row's expiration_date = <instant>, insert new row with
-- effective_date = <same instant>, expiration_date = NULL.  Never UPDATE values,
-- never DELETE history.
-- ---------------------------------------------------------------------------
CREATE TABLE component_prices (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    component_id    BIGINT      NOT NULL REFERENCES components(id),
    base_amount     NUMERIC(12,3) NOT NULL CHECK (base_amount > 0),  -- e.g. 1000 (g), 1
    base_price      NUMERIC(12,2) NOT NULL CHECK (base_price >= 0),  -- HUF for base_amount
    effective_date  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expiration_date TIMESTAMPTZ,                                      -- NULL = open-ended
    entry_date      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (expiration_date IS NULL OR expiration_date > effective_date),
    -- No two overlapping price windows for the same component (DB-enforced):
    EXCLUDE USING gist (
        component_id WITH =,
        tstzrange(effective_date, COALESCE(expiration_date, 'infinity'::timestamptz), '[)') WITH &&
    )
);
CREATE INDEX idx_component_prices_lookup
    ON component_prices (component_id, effective_date, expiration_date);

-- ---------------------------------------------------------------------------
-- CUSTOMERS — no hard delete; anonymize instead (§3.7). Auto-anonymized after
-- N years of inactivity (env-configurable, default 5y) by a scheduled job.
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name           TEXT        NOT NULL,
    contact        TEXT,                    -- phone / email / social, free text
    notes          TEXT,
    anonymized_at  TIMESTAMPTZ,             -- non-null once scrubbed
    entry_date     TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_customers_name ON customers (lower(name));

-- ---------------------------------------------------------------------------
-- OFFERS — one quote per cake. entry_date is BOTH the row-created stamp AND the
-- pricing reference date used to pick effective component_prices (§3.0/§3.4).
-- final_price is the manual price the chef communicates; calculated_price is
-- NEVER stored (computed by v_offer_cost).
-- ---------------------------------------------------------------------------
CREATE TABLE offers (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id  BIGINT      NOT NULL REFERENCES customers(id),  -- never cascade-delete customer
    due_date     TIMESTAMPTZ,                        -- Határidő
    theme        TEXT,                               -- Téma
    flavor       TEXT,                               -- Íz
    final_price  NUMERIC(12,2),                      -- manual (§3.2); NULL until set
    status       TEXT        NOT NULL DEFAULT 'draft'
                 CHECK (status IN ('draft', 'sent', 'accepted', 'rejected', 'done')),
    notes        TEXT,
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(), -- pricing reference date
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_offers_customer ON offers (customer_id);
CREATE INDEX idx_offers_entry    ON offers (entry_date);

-- ---------------------------------------------------------------------------
-- OFFER_COMPONENTS — the line items of an offer. amount is the quantity used;
-- for a 'service' component (Munkadíj/Rezsi, base_amount=1) it is the multiplier.
-- Same component from two applied templates => two separate lines (NOT merged).
-- Deleting an offer cascades here ONLY (never to customers/components).
-- ---------------------------------------------------------------------------
CREATE TABLE offer_components (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    offer_id     BIGINT      NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
    component_id BIGINT      NOT NULL REFERENCES components(id),
    amount       NUMERIC(12,3) NOT NULL CHECK (amount >= 0),
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_offer_components_offer     ON offer_components (offer_id);
CREATE INDEX idx_offer_components_component ON offer_components (component_id);

-- ---------------------------------------------------------------------------
-- RECIPES / RECIPE_ITEMS — templates = a saved offer, NOT scalable (§3.5).
-- Size is encoded in the name (e.g. "12 szeletes epres torta"). Applying a
-- template copies its items into an offer as offer_components lines.
-- Editable & deletable (delete cascades to recipe_items only).
-- ---------------------------------------------------------------------------
CREATE TABLE recipes (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         TEXT        NOT NULL,   -- carries the size/portion count
    notes        TEXT,
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE recipe_items (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recipe_id    BIGINT      NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    component_id BIGINT      NOT NULL REFERENCES components(id),
    amount       NUMERIC(12,3) NOT NULL CHECK (amount >= 0),
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now(),
    update_date  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_recipe_items_recipe ON recipe_items (recipe_id);

-- ---------------------------------------------------------------------------
-- STOCK_MOVEMENTS — inventory ledger, APPEND-ONLY (§3.3). Current stock of a
-- component = SUM(qty_delta). +delivery, -consumption. Carries offer_id so an
-- offer edit/delete can find & reverse the movements it created.
-- Warning-only at 0 (enforced in app, not DB).
-- ---------------------------------------------------------------------------
CREATE TABLE stock_movements (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    component_id BIGINT      NOT NULL REFERENCES components(id),
    qty_delta    NUMERIC(12,3) NOT NULL,   -- + inbound, - consumption
    reason       TEXT        NOT NULL
                 CHECK (reason IN ('delivery', 'order', 'correction')),
    offer_id     BIGINT      REFERENCES offers(id) ON DELETE CASCADE,  -- null for deliveries/corrections
    entry_date   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_stock_movements_component ON stock_movements (component_id);
CREATE INDEX idx_stock_movements_offer     ON stock_movements (offer_id) WHERE offer_id IS NOT NULL;

-- ============================================================================
-- VIEWS — cost is ALWAYS computed at runtime, never stored (§3.0).
-- ============================================================================

-- Per-line cost, using the price effective on the OFFER's entry_date.
-- Price selection (§3.4):
--   1. the price window covering the offer's entry_date, else
--   2. FALLBACK: the component's earliest-known price (covers the case where a
--      component created AFTER the offer is added during a later edit — the offer
--      date precedes the component's first price window). No data is mutated;
--      component_prices stays strictly append-only.
-- LATERAL picks exactly one price row per line with that priority, so a missing
-- window can never silently drop the line from the total.
CREATE VIEW v_offer_line_cost AS
SELECT
    oc.id             AS offer_component_id,
    oc.offer_id,
    oc.component_id,
    oc.amount,
    cp.base_amount,
    cp.base_price,
    (cp.covers = false) AS used_fallback_price,   -- true => earliest-price fallback applied
    -- price = (amount / base_amount) * base_price ; rounded to whole HUF like the sheet
    ROUND((oc.amount / cp.base_amount) * cp.base_price, 0) AS line_price
FROM offer_components oc
JOIN offers o ON o.id = oc.offer_id
JOIN LATERAL (
    SELECT
        p.base_amount,
        p.base_price,
        (o.entry_date <@ tstzrange(p.effective_date,
             COALESCE(p.expiration_date, 'infinity'::timestamptz), '[)')) AS covers
    FROM component_prices p
    WHERE p.component_id = oc.component_id
    ORDER BY
        -- prefer the covering window; otherwise fall back to the earliest price
        (o.entry_date <@ tstzrange(p.effective_date,
             COALESCE(p.expiration_date, 'infinity'::timestamptz), '[)')) DESC,
        p.effective_date ASC
    LIMIT 1
) cp ON true;

-- Offer total (calculated_price) = sum of its line costs.
CREATE VIEW v_offer_cost AS
SELECT
    o.id AS offer_id,
    COALESCE(SUM(lc.line_price), 0) AS calculated_price,
    o.final_price,
    o.final_price - COALESCE(SUM(lc.line_price), 0) AS price_delta  -- final - calculated
FROM offers o
LEFT JOIN v_offer_line_cost lc ON lc.offer_id = o.id
GROUP BY o.id, o.final_price;

-- Current stock level per component.
CREATE VIEW v_component_stock AS
SELECT component_id, COALESCE(SUM(qty_delta), 0) AS on_hand
FROM stock_movements
GROUP BY component_id;

-- ============================================================================
-- SEED — initial groups (§3.1). Components/prices seeded by the import script.
-- ============================================================================
INSERT INTO groups (name, sort_order) VALUES
    ('Alap',     10),   -- Munkadíj + Rezsi (service components)
    ('Piskóta',  20),
    ('Töltelék', 30),
    ('Burkolat', 40),
    ('Dekor',    50),
    ('Doboz',    60);   -- boxes + Alátét (stock items)
