from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ``|`` is forbidden because the velocity member encoding is pipe-delimited.
    owner_ref: str = Field(min_length=1, max_length=64, pattern=r"^[^|]+$")
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    account_type: Literal["customer", "treasury"] = "customer"
    home_country: str = Field(default="US", pattern=r"^[A-Z]{2}$")


class AccountResponse(BaseModel):
    id: uuid.UUID
    owner_ref: str
    account_type: str
    currency: str
    balance_minor: int
    home_country: str
    created_at: datetime
