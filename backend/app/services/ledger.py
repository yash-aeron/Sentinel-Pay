"""Double-entry posting.

Sign convention (customer wallet view):

    debit  -> money leaves the account (balance decreases)
    credit -> money enters the account (balance increases)

Every transaction produces exactly one debit and one credit of equal size, so
the signed sum across the whole ledger is always zero.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import LedgerIntegrityError
from app.db.models import Account, LedgerEntry, Transaction


async def post_transfer_entries(
    session: AsyncSession,
    *,
    transaction: Transaction,
    source: Account,
    destination: Account,
    amount_minor: int,
) -> None:
    """Write both legs. Caller must already have applied the balance change."""
    if amount_minor <= 0:
        raise LedgerIntegrityError(
            "ledger amount must be positive", amount_minor=amount_minor
        )

    debit = LedgerEntry(
        transaction_id=transaction.id,
        account_id=source.id,
        direction="debit",
        amount_minor=amount_minor,
        balance_after_minor=source.balance_minor,
    )
    credit = LedgerEntry(
        transaction_id=transaction.id,
        account_id=destination.id,
        direction="credit",
        amount_minor=amount_minor,
        balance_after_minor=destination.balance_minor,
    )

    if debit.amount_minor != credit.amount_minor:  # pragma: no cover
        raise LedgerIntegrityError(
            "unbalanced transaction",
            debit_minor=debit.amount_minor,
            credit_minor=credit.amount_minor,
        )

    session.add_all([debit, credit])


async def entries_for(
    session: AsyncSession, transaction_id: uuid.UUID
) -> list[LedgerEntry]:
    result = await session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.transaction_id == transaction_id)
        .order_by(LedgerEntry.id)
    )
    return list(result)


def zero_sum(entries: list[LedgerEntry]) -> bool:
    total = 0
    for entry in entries:
        total += entry.amount_minor if entry.direction == "credit" else -entry.amount_minor
    return total == 0
