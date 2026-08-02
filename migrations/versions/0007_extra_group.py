"""An "Extra" component group for last-minute add-ons.

Candles, sparklers and the like, asked for at handover on an already-accepted
offer. They are priced differently from every other group: adding one must RAISE
the final price rather than eat into the margin (see offer-form.js), so they need
their own group to be recognised by. Sorted between Dekor and Doboz.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_extra_group"
down_revision = "0006_app_settings"
branch_labels = None
depends_on = None

_NAME = "Extra"


def upgrade() -> None:
    # Idempotent: `name` is UNIQUE, and an installation may already have added it
    # by hand from the Csoportok page.
    op.execute(
        sa.text(
            "INSERT INTO groups (name, sort_order) VALUES (:name, 55) ON CONFLICT (name) DO NOTHING"
        ).bindparams(name=_NAME)
    )


def downgrade() -> None:
    # Only drop it when unused — a component assigned to it would be orphaned.
    op.execute(
        sa.text(
            "DELETE FROM groups g WHERE g.name = :name "
            "AND NOT EXISTS (SELECT 1 FROM components c WHERE c.group_id = g.id)"
        ).bindparams(name=_NAME)
    )
