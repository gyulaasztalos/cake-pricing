"""A 'cancelled' (Lemondás) offer status.

The customer accepted, then pulled out — typically days before the due date. It
is distinct from 'rejected' (Elutasítva), which means the offer was never won at
all: a cancellation still counts as a won offer, it just never got delivered.

The status lives in a CHECK constraint rather than a PG enum, so widening it is a
drop-and-recreate of that constraint.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_cancelled"
down_revision = "0007_extra_group"
branch_labels = None
depends_on = None

_CONSTRAINT = "offers_status_check"
_WITHOUT = "status IN ('draft', 'sent', 'accepted', 'deposit', 'rejected', 'done')"
_WITH = "status IN ('draft', 'sent', 'accepted', 'deposit', 'rejected', 'done', 'cancelled')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "offers", type_="check")
    op.create_check_constraint(_CONSTRAINT, "offers", sa.text(_WITH))


def downgrade() -> None:
    # Any offer already cancelled would violate the narrower constraint, so park
    # those back on 'rejected' — the closest state the old schema can express.
    op.execute(sa.text("UPDATE offers SET status = 'rejected' WHERE status = 'cancelled'"))
    op.drop_constraint(_CONSTRAINT, "offers", type_="check")
    op.create_check_constraint(_CONSTRAINT, "offers", sa.text(_WITHOUT))
