from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, status
from fastapi.responses import Response
from sqlalchemy import select

from app.api.deps import PaymentsDep, ScopeDep, SessionMakerDep
from app.core.errors import TransactionNotFound
from app.db.models import LedgerEntry, Transaction
from app.schemas.transaction import (
    LedgerEntryResponse,
    TransactionDetail,
    TransactionResponse,
    TransferCreate,
)
from app.services.payments import PaymentCommand

router = APIRouter(tags=["transactions"])

IdempotencyKeyDep = Annotated[
    str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
]


@router.post(
    "/transactions",
    status_code=status.HTTP_201_CREATED,
    response_model=TransactionResponse,
    responses={
        201: {"description": "Transfer settled (or replayed from a previous identical request)"},
        202: {"description": "Transfer held for review; no money has moved"},
        402: {"description": "Transfer declined by the risk engine; no money has moved"},
        409: {"description": "Insufficient funds, or the same key is still in flight"},
        422: {"description": "Validation failure, or Idempotency-Key reused with a different body"},
        503: {"description": "Payment recorded but the audit log is temporarily unavailable"},
    },
)
async def create_transaction(
    payload: TransferCreate,
    idempotency_key: IdempotencyKeyDep,
    scope: ScopeDep,
    payments: PaymentsDep,
) -> Response:
    command = PaymentCommand(
        scope=scope,
        idempotency_key=idempotency_key,
        from_account_id=payload.from_account_id,
        to_account_id=payload.to_account_id,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        description=payload.description,
        country=payload.country,
        merchant_id=payload.merchant_id,
        device_id=payload.device_id,
    )
    outcome = await payments.execute(command)
    # Returned as raw text so a replay is byte-identical to the first response.
    return Response(
        content=outcome.body_json,
        status_code=outcome.status_code,
        media_type="application/json",
        headers={"Idempotent-Replay": "true" if outcome.replayed else "false"},
    )


@router.get(
    "/transactions/{transaction_id}",
    response_model=TransactionDetail,
    responses={404: {"description": "Unknown transaction"}},
)
async def get_transaction(
    transaction_id: uuid.UUID, sessionmaker: SessionMakerDep
) -> TransactionDetail:
    async with sessionmaker() as session:
        transaction = await session.get(Transaction, transaction_id)
        if transaction is None:
            raise TransactionNotFound(f"transaction {transaction_id} not found")

        rows = (
            await session.scalars(
                select(LedgerEntry)
                .where(LedgerEntry.transaction_id == transaction_id)
                .order_by(LedgerEntry.id)
            )
        ).all()

        return TransactionDetail(
            **_response_fields(transaction),
            entries=[
                LedgerEntryResponse(
                    id=row.id,
                    account_id=row.account_id,
                    direction=row.direction,  # type: ignore[arg-type]
                    amount_minor=row.amount_minor,
                    balance_after_minor=row.balance_after_minor,
                    created_at=row.created_at,
                )
                for row in rows
            ],
        )


@router.get("/transactions", response_model=list[TransactionResponse])
async def list_transactions(
    sessionmaker: SessionMakerDep,
    limit: int = 50,
    status: str | None = None,  # noqa: A002 - query parameter name
) -> list[TransactionResponse]:
    """Recent transactions, newest first.

    ``status`` narrows to one state; the analyst queue is
    ``/transactions?status=held``.
    """
    limit = max(1, min(limit, 200))
    query = select(Transaction).order_by(Transaction.created_at.desc()).limit(limit)
    if status is not None:
        query = query.where(Transaction.status == status)
    async with sessionmaker() as session:
        rows = (await session.scalars(query)).all()
    return [TransactionResponse(**_response_fields(row)) for row in rows]


def _response_fields(transaction: Transaction) -> dict:
    return {
        "id": transaction.id,
        "status": transaction.status,
        "from_account_id": transaction.from_account_id,
        "to_account_id": transaction.to_account_id,
        "amount_minor": transaction.amount_minor,
        "currency": transaction.currency.strip(),
        "description": transaction.description,
        "created_at": transaction.created_at,
        "decision": transaction.decision,
        "risk_score": transaction.risk_score,
        "model_version": transaction.model_version,
        "rules_fired": list(transaction.rules_fired or []),
        "review_status": transaction.review_status,
    }


__all__ = ["router"]
