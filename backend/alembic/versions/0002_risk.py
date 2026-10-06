"""risk scoring columns, per-user velocity and held/declined states

Revision ID: 0002_risk
Revises: 0001_initial
Create Date: 2026-10-24

Hand-written. The important part is the ``user_ref`` backfill: existing rows
predate per-user velocity, so they inherit the source account's owner. Only
then does the column become NOT NULL.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002_risk"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "accounts",
        sa.Column(
            "home_country", sa.CHAR(length=2), server_default="US", nullable=False
        ),
    )

    op.add_column("transactions", sa.Column("user_ref", sa.String(length=64), nullable=True))
    op.execute(
        """
        UPDATE transactions t
        SET user_ref = a.owner_ref
        FROM accounts a
        WHERE a.id = t.from_account_id
        """
    )
    op.alter_column("transactions", "user_ref", nullable=False)

    op.add_column(
        "transactions",
        sa.Column("country", sa.CHAR(length=2), server_default="US", nullable=False),
    )
    op.add_column("transactions", sa.Column("merchant_id", sa.String(length=64), nullable=True))
    op.add_column("transactions", sa.Column("device_id", sa.String(length=64), nullable=True))
    op.add_column("transactions", sa.Column("decision", sa.String(length=16), nullable=True))
    op.add_column("transactions", sa.Column("risk_score", sa.Float(), nullable=True))
    op.add_column(
        "transactions", sa.Column("model_version", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "transactions",
        sa.Column(
            "rules_fired",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "transactions",
        sa.Column("features", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "transactions",
        sa.Column(
            "velocity_degraded",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "transactions", sa.Column("review_status", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "transactions", sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True)
    )

    # Phase 1 could only produce 'settled'. Held money is Phase 2's review
    # queue; declined attempts are recorded because attempts are the signal.
    op.drop_constraint(
        op.f("ck_transactions_valid_status"), "transactions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_transactions_valid_status"),
        "transactions",
        "status IN ('settled', 'held', 'declined')",
    )
    op.create_check_constraint(
        op.f("ck_transactions_valid_decision"),
        "transactions",
        "decision IS NULL OR decision IN ('APPROVE', 'REVIEW', 'DECLINE')",
    )
    op.create_check_constraint(
        op.f("ck_transactions_valid_review_status"),
        "transactions",
        "review_status IS NULL OR review_status IN ('pending', 'approved', 'rejected')",
    )

    op.create_index(
        "ix_transactions_user_ref_created_at",
        "transactions",
        ["user_ref", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_transactions_decision_created_at",
        "transactions",
        ["decision", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_decision_created_at", table_name="transactions")
    op.drop_index("ix_transactions_user_ref_created_at", table_name="transactions")

    op.drop_constraint(
        op.f("ck_transactions_valid_review_status"), "transactions", type_="check"
    )
    op.drop_constraint(
        op.f("ck_transactions_valid_decision"), "transactions", type_="check"
    )
    # A held row has no legal representation in the Phase 1 schema. Downgrading
    # is only meaningful before review traffic exists, so fail loudly rather
    # than silently promoting held payments to settled.
    op.execute("DELETE FROM transactions WHERE status <> 'settled'")
    op.drop_constraint(
        op.f("ck_transactions_valid_status"), "transactions", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_transactions_valid_status"), "transactions", "status IN ('settled')"
    )

    for column in (
        "scored_at",
        "review_status",
        "velocity_degraded",
        "features",
        "rules_fired",
        "model_version",
        "risk_score",
        "decision",
        "device_id",
        "merchant_id",
        "country",
        "user_ref",
    ):
        op.drop_column("transactions", column)

    op.drop_column("accounts", "home_country")
