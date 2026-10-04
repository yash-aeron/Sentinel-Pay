from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    AccountNotFound,
    ConcurrentIdempotentRequest,
    CurrencyMismatch,
    IdempotencyKeyReuse,
    InsufficientFunds,
    SameAccountTransfer,
)
from app.db.models import Account, IdempotencyKey, Transaction
from app.schemas.transaction import TransactionResponse
from app.services import idempotency, ledger


@dataclass(frozen=True, slots=True)
class TransferCommand:
    scope: str
    idempotency_key: str
    from_account_id: uuid.UUID
    to_account_id: uuid.UUID
    amount_minor: int
    currency: str
    description: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "from_account_id": str(self.from_account_id),
            "to_account_id": str(self.to_account_id),
            "amount_minor": self.amount_minor,
            "currency": self.currency,
            "description": self.description,
        }


@dataclass(frozen=True, slots=True)
class TransferOutcome:
    """``body_json`` is the serialised response, stored verbatim so a replay is
    byte-identical to the original answer."""

    body_json: str
    status_code: int
    replayed: bool


async def execute(
    sessionmaker: async_sessionmaker[AsyncSession], command: TransferCommand
) -> TransferOutcome:
    request_hash = idempotency.canonical_hash(command.canonical_payload())

    async with sessionmaker() as session, session.begin():
        await idempotency.acquire_key_lock(
            session, scope=command.scope, key=command.idempotency_key
        )

        claim = await idempotency.find_claim(
            session, scope=command.scope, key=command.idempotency_key
        )
        if claim is not None:
            return _resolve_existing(claim, request_hash)

        claim = idempotency.new_claim(
            scope=command.scope,
            key=command.idempotency_key,
            request_hash=request_hash,
        )
        session.add(claim)
        await session.flush()

        body, status_code, transaction_id = await _perform(session, command)
        body_json = json.dumps(body, separators=(",", ":"))

        claim.status = "completed"
        claim.response_status = status_code
        claim.response_body = body_json
        claim.transaction_id = transaction_id

        return TransferOutcome(body_json=body_json, status_code=status_code, replayed=False)


def _resolve_existing(claim: IdempotencyKey, request_hash: str) -> TransferOutcome:
    if claim.request_hash != request_hash:
        raise IdempotencyKeyReuse(
            "this Idempotency-Key was already used with a different request body",
            key=claim.key,
        )
    if claim.status != "completed" or claim.response_body is None:
        raise ConcurrentIdempotentRequest(
            "a request with this Idempotency-Key is still in flight", key=claim.key
        )
    return TransferOutcome(
        body_json=claim.response_body,
        status_code=claim.response_status or 201,
        replayed=True,
    )


async def _perform(
    session: AsyncSession, command: TransferCommand
) -> tuple[dict[str, Any], int, uuid.UUID]:
    if command.from_account_id == command.to_account_id:
        raise SameAccountTransfer("source and destination accounts must differ")

    accounts = await _lock_accounts_in_order(
        session, [command.from_account_id, command.to_account_id]
    )
    source = accounts[command.from_account_id]
    destination = accounts[command.to_account_id]

    if source.currency != destination.currency or source.currency != command.currency:
        raise CurrencyMismatch(
            "accounts and request must share one currency",
            request_currency=command.currency,
            source_currency=source.currency,
            destination_currency=destination.currency,
        )

    # Checked while holding the lock. Checking earlier would be a TOCTOU bug.
    if source.account_type != "treasury" and source.balance_minor < command.amount_minor:
        raise InsufficientFunds(
            "source account has insufficient funds",
            balance_minor=source.balance_minor,
            requested_minor=command.amount_minor,
        )

    source.balance_minor -= command.amount_minor
    destination.balance_minor += command.amount_minor

    transaction = Transaction(
        from_account_id=source.id,
        to_account_id=destination.id,
        amount_minor=command.amount_minor,
        currency=command.currency,
        status="settled",
        description=command.description,
    )
    session.add(transaction)
    await session.flush()

    await ledger.post_transfer_entries(
        session,
        transaction=transaction,
        source=source,
        destination=destination,
        amount_minor=command.amount_minor,
    )

    body = TransactionResponse(
        id=transaction.id,
        status=transaction.status,
        from_account_id=transaction.from_account_id,
        to_account_id=transaction.to_account_id,
        amount_minor=transaction.amount_minor,
        currency=transaction.currency.strip(),
        description=transaction.description,
        created_at=transaction.created_at,
    ).model_dump(mode="json")

    return body, 201, transaction.id


async def _lock_accounts_in_order(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Account]:
    """Lock every participant in ascending id order, one statement each.

    Two statements rather than a single ``WHERE id = ANY(...) ORDER BY id FOR
    UPDATE``: Postgres does not promise the sort happens before rows are
    locked, so the ordered-set form looks safe and is not. With a deterministic
    order, A->B and B->A can never deadlock.
    """
    locked: dict[uuid.UUID, Account] = {}
    for account_id in sorted(set(account_ids)):
        account = await session.scalar(
            select(Account).where(Account.id == account_id).with_for_update()
        )
        if account is None:
            raise AccountNotFound(f"account {account_id} not found", account_id=str(account_id))
        locked[account_id] = account
    return locked
