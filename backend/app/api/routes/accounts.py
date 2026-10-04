from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from app.api.deps import SessionMakerDep
from app.core.errors import AccountNotFound
from app.db.models import Account
from app.schemas.account import AccountCreate, AccountResponse

router = APIRouter(tags=["accounts"])


@router.post("/accounts", status_code=status.HTTP_201_CREATED, response_model=AccountResponse)
async def create_account(payload: AccountCreate, sessionmaker: SessionMakerDep) -> AccountResponse:
    async with sessionmaker() as session, session.begin():
        account = Account(
            owner_ref=payload.owner_ref,
            currency=payload.currency,
            account_type=payload.account_type,
            balance_minor=0,
        )
        session.add(account)
        await session.flush()
        return AccountResponse(
            id=account.id,
            owner_ref=account.owner_ref,
            account_type=account.account_type,
            currency=account.currency.strip(),
            balance_minor=account.balance_minor,
            created_at=account.created_at,
        )


@router.get(
    "/accounts/{account_id}", response_model=AccountResponse, responses={404: {}}
)
async def get_account(account_id: uuid.UUID, sessionmaker: SessionMakerDep) -> AccountResponse:
    async with sessionmaker() as session:
        account = await session.get(Account, account_id)
        if account is None:
            raise AccountNotFound(f"account {account_id} not found")
        return AccountResponse(
            id=account.id,
            owner_ref=account.owner_ref,
            account_type=account.account_type,
            currency=account.currency.strip(),
            balance_minor=account.balance_minor,
            created_at=account.created_at,
        )
