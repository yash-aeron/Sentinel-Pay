from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class LedgerEntry(Base):
    """Append-only double-entry line.

    Never updated, never deleted. Corrections are new compensating
    transactions. ``balance_after_minor`` is captured at posting time so an
    auditor can replay balances without trusting current state.
    """

    __tablename__ = "ledger_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("transactions.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    __table_args__ = (
        CheckConstraint("direction IN ('debit', 'credit')", name="valid_direction"),
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        Index("ix_ledger_entries_transaction_id", "transaction_id"),
        Index("ix_ledger_entries_account_id_id", "account_id", "id"),
    )
