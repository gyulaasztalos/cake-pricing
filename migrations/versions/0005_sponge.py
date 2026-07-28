"""Offer sponge (Piskóta) base.

Optional free text, set by the cake-order intake (which sends the Hungarian
display name of the customer's choice) or by the chef on the offer form. Grouped
on the statistics page the same way flavor and theme are.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_sponge"
down_revision = "0004_paid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offers", sa.Column("sponge", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("offers", "sponge")
