from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class Transaction(Base):
    """One money movement between two accounts.

    Phase 1 only produces ``settled`` transactions. Phase 2 extends the status
    set with ``declined``/``review`` via an Alembic migration rather than
    pre-declaring states nothing can reach yet.
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
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="settled", server_default="settled"
    )
    description: Mapped[str | None] = mapped_column(String(280), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, index=True
    )

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("from_account_id <> to_account_id", name="distinct_accounts"),
        CheckConstraint("status IN ('settled')", name="valid_status"),
    )
