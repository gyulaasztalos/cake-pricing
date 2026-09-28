"""Payment lines: one offer can be paid in several instalments and methods.

A deposit by bank transfer and the rest in cash at handover, or a Revolut
payment, each become their own row. `offers.paid` stays as the denormalized SUM
of an offer's rows — the router rewrites both together on every save — so the
auto-status rule and every statistic keep reading the one column they always
have.

Existing data: every recorded Fizetve becomes a single 'cash' (Készpénz) line of
the same amount, the owner's call, since the method was never tracked before.
A Fizetve of 0 carried no information and would violate `amount > 0`, so it is
cleared to NULL — "nothing recorded", which is what an empty payment list means.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_offer_payments"
down_revision = "0008_cancelled"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "offer_payments",
        sa.Column("id", sa.BigInteger, sa.Identity(always=False), primary_key=True),
        sa.Column(
            "offer_id",
            sa.BigInteger,
            sa.ForeignKey("offers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Frozen here on purpose: a migration must not import the live constant.
        sa.Column("method", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("entry_date", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("update_date", TS, nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "method IN ('transfer', 'cash', 'revolut')", name="offer_payments_method_check"
        ),
        sa.CheckConstraint("amount > 0", name="offer_payments_amount_check"),
    )
    op.create_index("idx_offer_payments_offer", "offer_payments", ["offer_id"])

    op.execute(
        sa.text(
            "INSERT INTO offer_payments (offer_id, method, amount) "
            "SELECT id, 'cash', paid FROM offers WHERE paid > 0 ORDER BY id"
        )
    )
    op.execute(sa.text("UPDATE offers SET paid = NULL WHERE paid <= 0"))


def downgrade() -> None:
    # offers.paid already holds each offer's total, so dropping the lines loses
    # only the per-method split, never the amount.
    op.drop_index("idx_offer_payments_offer", table_name="offer_payments")
    op.drop_table("offer_payments")
