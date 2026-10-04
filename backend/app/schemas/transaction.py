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


class TransactionResponse(BaseModel):
    id: uuid.UUID
    status: str
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_minor: int
    currency: str
    description: str | None
    created_at: datetime


class LedgerEntryResponse(BaseModel):
    id: int
    account_id: uuid.UUID
    direction: Literal["debit", "credit"]
    amount_minor: int
    balance_after_minor: int
    created_at: datetime


class TransactionDetail(TransactionResponse):
    entries: list[LedgerEntryResponse]
