"""Analyst review: turn a held transaction into settled money or a decline.

The approve path is the only place outside ``PaymentService`` allowed to move
money, and it moves it through exactly the same machinery: accounts locked in
ascending id order, funds checked while holding the lock, double-entry legs
posted by the ledger module. An approval is therefore a *payment* that a
human authorised, not a flag flip — the ledger invariants apply to it
unchanged.

Idempotency is structural rather than keyed: a decision is a state transition
guarded by the transaction row lock, and re-sending a decision that already
happened returns the current state instead of erroring. Retrying after a
network blip cannot double-settle.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import (
    AccountNotFound,
    InsufficientFunds,
    TransactionNotFound,
)
from app.db.models import Account, Transaction
from app.schemas.transaction import TransactionResponse
from app.services import ledger
from app.services.events import EventBus

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"


class ReviewService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        *,
        events: EventBus | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._events = events

    async def decide(
        self, transaction_id: uuid.UUID, action: str, analyst: str
    ) -> TransactionResponse:
        if action not in (ACTION_APPROVE, ACTION_REJECT):
            raise ValueError(f"unknown review action: {action}")
        async with self._sessionmaker() as session, session.begin():
            # Row lock serialises two analysts clicking at the same moment.
            transaction = await session.scalar(
                select(Transaction)
                .where(Transaction.id == transaction_id)
                .with_for_update()
            )
            if transaction is None:
                raise TransactionNotFound(
                    f"transaction {transaction_id} not found",
                    transaction_id=str(transaction_id),
                )

            if transaction.status != "held" or transaction.review_status != "pending":
                # Already settled, declined, or re-reviewed: the caller is
                # behind reality. Returning the row is the idempotent answer.
                return _response(transaction)

            if action == ACTION_APPROVE:
                await self._settle(session, transaction)
            else:
                transaction.status = "declined"
                transaction.review_status = "rejected"

            response = _response(transaction)

        if self._events is not None:
            self._events.publish(
                "review.decided",
                transaction_id=str(response.id),
                action=action,
                analyst=analyst,
                status=response.status,
                decision=response.decision,
                review_status=response.review_status,
            )
        return response

    async def _settle(self, session: AsyncSession, transaction: Transaction) -> None:
        accounts = await _lock_accounts_in_order(
            session, [transaction.from_account_id, transaction.to_account_id]
        )
        source = accounts[transaction.from_account_id]
        destination = accounts[transaction.to_account_id]

        # The funds check that was skipped when the payment was held happens
        # now, under the same locks as the movement it guards.
        if (
            source.account_type != "treasury"
            and source.balance_minor < transaction.amount_minor
        ):
            # Held stays held: the analyst decides whether to reject.
            raise InsufficientFunds(
                "source account no longer has sufficient funds; the payment is still held",
                balance_minor=source.balance_minor,
                requested_minor=transaction.amount_minor,
                transaction_id=str(transaction.id),
            )

        source.balance_minor -= transaction.amount_minor
        destination.balance_minor += transaction.amount_minor

        transaction.status = "settled"
        transaction.review_status = "approved"

        await ledger.post_transfer_entries(
            session,
            transaction=transaction,
            source=source,
            destination=destination,
            amount_minor=transaction.amount_minor,
        )


async def _lock_accounts_in_order(
    session: AsyncSession, account_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Account]:
    """Same discipline as ``payments._lock_accounts_in_order``: ascending id,
    one statement each, repopulating stale identity-map rows."""
    locked: dict[uuid.UUID, Account] = {}
    for account_id in sorted(set(account_ids)):
        account = await session.scalar(
            select(Account)
            .where(Account.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise AccountNotFound(f"account {account_id} not found", account_id=str(account_id))
        locked[account_id] = account
    return locked


def _response(transaction: Transaction) -> TransactionResponse:
    return TransactionResponse(
        id=transaction.id,
        status=transaction.status,
        from_account_id=transaction.from_account_id,
        to_account_id=transaction.to_account_id,
        amount_minor=transaction.amount_minor,
        currency=transaction.currency.strip(),
        description=transaction.description,
        created_at=transaction.created_at,
        decision=transaction.decision,
        risk_score=transaction.risk_score,
        model_version=transaction.model_version,
        rules_fired=list(transaction.rules_fired or []),
        review_status=transaction.review_status,
    )


__all__ = ["ACTION_APPROVE", "ACTION_REJECT", "ReviewService"]
