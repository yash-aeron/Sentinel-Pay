from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TransferCreate(BaseModel):
    # extra="forbid" matters for idempotency: an unknown field must fail loudly
    # rather than silently change a body that hashes the same as a previous one.
    model_config = ConfigDict(extra="forbid")

    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_minor: int = Field(gt=0, le=10**12)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    description: str | None = Field(default=None, max_length=280)
    # Risk context supplied by the caller. ``|`` is forbidden because velocity
    # members are pipe-delimited; whitespace is forbidden because these are ids.
    country: str = Field(default="US", pattern=r"^[A-Z]{2}$")
    merchant_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[^|\s]+$"
    )
    device_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[^|\s]+$"
    )


class TransactionResponse(BaseModel):
    id: uuid.UUID
    status: str
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_minor: int
    currency: str
    description: str | None
    created_at: datetime
    decision: str | None = None
    risk_score: float | None = None
    model_version: str | None = None
    rules_fired: list[str] = Field(default_factory=list)
    review_status: str | None = None


class LedgerEntryResponse(BaseModel):
    id: int
    account_id: uuid.UUID
    direction: Literal["debit", "credit"]
    amount_minor: int
    balance_after_minor: int
    created_at: datetime


class TransactionDetail(TransactionResponse):
    entries: list[LedgerEntryResponse]
