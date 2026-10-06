from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class Transaction(Base):
    """One payment attempt between two accounts.

    A row exists whether the attempt settled, was held for review, or was
    declined: the attempt itself is the unit of fraud analysis. Only
    ``settled`` rows move money.

    Risk columns are denormalised on purpose. An analyst looking at a payment
    should not need to join an ML system to see why it was scored the way it
    was, and an auditor should be able to reconstruct the decision years later
    from Postgres alone.
    """

    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    from_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True
    )
    to_account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False, index=True
    )
    # Velocity is measured per human, not per account: a user can transact from
    # a wallet and a card account, and card testing would otherwise look like
    # two well-behaved accounts.
    user_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    country: Mapped[str] = mapped_column(
        CHAR(2), nullable=False, default="US", server_default="US"
    )
    merchant_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="settled", server_default="settled"
    )
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)

    decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    rules_fired: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    velocity_degraded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    review_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="distinct_accounts"),
        CheckConstraint(
            "status IN ('settled', 'held', 'declined')", name="valid_status"
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('APPROVE', 'REVIEW', 'DECLINE')",
            name="valid_decision",
        ),
        CheckConstraint(
            "review_status IS NULL OR review_status IN "
            "('pending', 'approved', 'rejected')",
            name="valid_review_status",
        ),
        Index("ix_transactions_user_ref_created_at", "user_ref", "created_at"),
        Index("ix_transactions_decision_created_at", "decision", "created_at"),
    )
