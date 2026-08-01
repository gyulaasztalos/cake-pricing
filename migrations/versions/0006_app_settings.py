"""Chef-editable settings: the default business-profit percentage.

The profit % itself is NEVER stored on an offer — like cost, it is DERIVED
(final_price / calculated_price - 1), so it can never drift out of step with the
price and legacy offers show their real implied margin. Only the default used to
prefill a NEW offer needs persisting, hence the app_settings singleton.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.config import settings

revision = "0006_app_settings"
down_revision = "0005_sponge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Chef-editable settings (Beállítások). Seeded with the deployment default so
    # the row always exists and the app never has to special-case "no settings yet".
    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("default_profit_pct", sa.Numeric(7, 2), nullable=False),
        sa.Column(
            "update_date", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.execute(
        sa.text("INSERT INTO app_settings (id, default_profit_pct) VALUES (1, :pct)").bindparams(
            pct=settings.default_profit_pct
        )
    )


def downgrade() -> None:
    op.drop_table("app_settings")
