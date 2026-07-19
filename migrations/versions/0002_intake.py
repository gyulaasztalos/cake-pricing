"""Customer intake (§8a): portions, source, request_date; nullable entry_date.

External drafts arrive from the public cake-order app WITHOUT an entry_date —
the pricing reference date is set when the chef first saves the offer. The
cost view gains an explicit entry_date guard so an unpriced offer can never
be silently priced by the fallback path.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_intake"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

LINE_COST_VIEW_GUARDED = """
    CREATE VIEW v_offer_line_cost AS
    SELECT
        oc.id             AS offer_component_id,
        oc.offer_id,
        oc.component_id,
        oc.amount,
        cp.base_amount,
        cp.base_price,
        (cp.covers = false) AS used_fallback_price,
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
            (o.entry_date <@ tstzrange(p.effective_date,
                 COALESCE(p.expiration_date, 'infinity'::timestamptz), '[)')) DESC,
            p.effective_date ASC
        LIMIT 1
    ) cp ON true
    WHERE o.entry_date IS NOT NULL
"""

LINE_COST_VIEW_ORIGINAL = LINE_COST_VIEW_GUARDED.replace("\n    WHERE o.entry_date IS NOT NULL", "")


def upgrade() -> None:
    op.add_column("offers", sa.Column("portions", sa.Integer(), nullable=True))
    op.add_column(
        "offers",
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'internal'")),
    )
    op.add_column("offers", sa.Column("request_date", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        "offers_source_check", "offers", "source IN ('internal', 'external')"
    )
    op.alter_column("offers", "entry_date", nullable=True)

    # v_offer_cost depends on v_offer_line_cost → recreate both.
    op.execute("DROP VIEW v_offer_cost")
    op.execute("DROP VIEW v_offer_line_cost")
    op.execute(LINE_COST_VIEW_GUARDED)
    op.execute(
        """
        CREATE VIEW v_offer_cost AS
        SELECT
            o.id AS offer_id,
            COALESCE(SUM(lc.line_price), 0) AS calculated_price,
            o.final_price,
            o.final_price - COALESCE(SUM(lc.line_price), 0) AS price_delta
        FROM offers o
        LEFT JOIN v_offer_line_cost lc ON lc.offer_id = o.id
        GROUP BY o.id, o.final_price
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW v_offer_cost")
    op.execute("DROP VIEW v_offer_line_cost")
    op.execute(LINE_COST_VIEW_ORIGINAL)
    op.execute(
        """
        CREATE VIEW v_offer_cost AS
        SELECT
            o.id AS offer_id,
            COALESCE(SUM(lc.line_price), 0) AS calculated_price,
            o.final_price,
            o.final_price - COALESCE(SUM(lc.line_price), 0) AS price_delta
        FROM offers o
        LEFT JOIN v_offer_line_cost lc ON lc.offer_id = o.id
        GROUP BY o.id, o.final_price
        """
    )
    op.execute(
        "UPDATE offers SET entry_date = COALESCE(request_date, now()) WHERE entry_date IS NULL"
    )
    op.alter_column("offers", "entry_date", nullable=False)
    op.drop_constraint("offers_source_check", "offers")
    op.drop_column("offers", "request_date")
    op.drop_column("offers", "source")
    op.drop_column("offers", "portions")
