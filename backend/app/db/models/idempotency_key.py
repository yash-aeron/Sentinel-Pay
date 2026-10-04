from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class IdempotencyKey(Base):
    """Claim row for an ``Idempotency-Key``.

    The row is written in the *same* database transaction as the ledger
    movement, so "charged but no record" and "recorded but not charged" are
    both impossible.

    ``status`` is ``in_progress`` only for the brief window before the response
    snapshot is known; a committed row is always ``completed``.
    """

    __tablename__ = "idempotency_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="in_progress", server_default="in_progress"
    )
    response_status: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Stored as the exact response text, not JSONB: JSONB reorders object keys,
    # which would make a replay differ byte-for-byte from the original answer.
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("scope", "key"),
        CheckConstraint(
            "status IN ('in_progress', 'completed')", name="valid_status"
        ),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )
