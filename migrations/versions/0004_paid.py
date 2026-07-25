"""Offer 'paid' amount (Fizetve) + new 'deposit' status.

Adds a nullable `paid` column to offers and widens the status CHECK to allow a
new 'deposit' (Előlegezve) state: on save, a paid amount below the final price
sets 'deposit', at or above it sets 'done'.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_paid"
down_revision = "0003_price_sync"
branch_labels = None
depends_on = None

_OLD = "status IN ('draft', 'sent', 'accepted', 'rejected', 'done')"
_NEW = "status IN ('draft', 'sent', 'accepted', 'deposit', 'rejected', 'done')"


def upgrade() -> None:
    op.add_column("offers", sa.Column("paid", sa.Numeric(12, 2), nullable=True))
    op.drop_constraint("offers_status_check", "offers", type_="check")
    op.create_check_constraint("offers_status_check", "offers", _NEW)


def downgrade() -> None:
    # Assumes no rows use the new 'deposit' status (the CHECK would reject them).
    op.drop_constraint("offers_status_check", "offers", type_="check")
    op.create_check_constraint("offers_status_check", "offers", _OLD)
    op.drop_column("offers", "paid")
