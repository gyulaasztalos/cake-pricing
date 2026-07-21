"""Daily price sync (§ automatic price update).

Adds the optional "Termék azonosító" (product_id) to components, a
price_missing_at marker the sync job sets when a configured id is not found in
the árfigyelő file (drives a UI warning), and a singleton price_sync_state row
recording the last successful run (exposed on /metrics for staleness alerting).

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_price_sync"
down_revision = "0002_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("components", sa.Column("product_id", sa.Text(), nullable=True))
    op.add_column(
        "components", sa.Column("price_missing_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Fast lookup of components that have a product_id (the job's working set).
    op.create_index(
        "idx_components_product_id",
        "components",
        ["product_id"],
        postgresql_where=sa.text("product_id IS NOT NULL"),
    )
    op.create_table(
        "price_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "update_date",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # Seed the singleton row. last_success_at starts at deploy time so the
    # staleness clock (>25h) begins now: the first real run must land within a
    # day, and a never-running job still alarms — without a false alarm the
    # moment the migration applies.
    op.execute("INSERT INTO price_sync_state (id, last_success_at) VALUES (1, now())")


def downgrade() -> None:
    op.drop_table("price_sync_state")
    op.drop_index("idx_components_product_id", table_name="components")
    op.drop_column("components", "price_missing_at")
    op.drop_column("components", "product_id")
