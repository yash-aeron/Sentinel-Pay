from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CHAR, BigInteger, CheckConstraint, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.time import utcnow
from app.db.base import Base


class Account(Base):
    """A money-holding account.

    ``customer`` accounts may never go negative. ``treasury`` is the system
    mirror account: its balance is the negative of total customer float, which
    is where opening balances come from. Both are real ledger participants, so
    the global zero-sum invariant still holds.
    """

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default="customer", server_default="customer"
    )
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="USD")
    # Country the account holder normally transacts from. Used only for the
    # cross-border signal; not a compliance attribute.
    home_country: Mapped[str] = mapped_column(
        CHAR(2), nullable=False, default="US", server_default="US"
    )
    balance_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        CheckConstraint("account_type IN ('customer', 'treasury')", name="valid_type"),
        # The overdraft rule lives in the schema, not only in the service layer:
        # a future code path must not be able to quietly create one.
        CheckConstraint(
            "account_type = 'treasury' OR balance_minor >= 0", name="no_overdraft"
        ),
        CheckConstraint("char_length(currency) = 3", name="currency_len"),
    )
