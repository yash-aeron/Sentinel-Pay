"""Seed a treasury and three funded demo customers.

Funding goes *through the ledger* as a transfer from the treasury account, never
as a direct balance UPDATE, so the zero-sum invariant holds from the first row.
Deterministic idempotency keys make the script safe to re-run.

    python scripts/seed.py
"""

from __future__ import annotations

import asyncio
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import Account
from app.db.session import create_engine, create_sessionmaker
from app.services.payments import PaymentCommand, PaymentService

TREASURY_OWNER = "system-treasury"
SEED_ACCOUNTS: tuple[tuple[str, int], ...] = (
    ("alice", 100_000),
    ("bob", 100_000),
    ("carol", 100_000),
)


async def ensure_account(
    session: AsyncSession, owner_ref: str, account_type: str
) -> Account:
    existing = await session.scalar(
        select(Account).where(
            Account.owner_ref == owner_ref, Account.account_type == account_type
        )
    )
    if existing is not None:
        return existing

    account = Account(
        id=uuid.uuid4(),
        owner_ref=owner_ref,
        account_type=account_type,
        currency="USD",
        home_country="US",
        balance_minor=0,
    )
    session.add(account)
    await session.flush()
    return account


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_size=5)
    sessionmaker = create_sessionmaker(engine)
    # Seeding is setup, not customer traffic: no scoring, no audit.
    payments = PaymentService(sessionmaker)

    try:
        async with sessionmaker() as session, session.begin():
            treasury = await ensure_account(session, TREASURY_OWNER, "treasury")
            customers = {
                owner: await ensure_account(session, owner, "customer")
                for owner, _ in SEED_ACCOUNTS
            }
            treasury_id = treasury.id

        for owner, amount_minor in SEED_ACCOUNTS:
            account = customers[owner]
            if account.balance_minor != 0:
                print(f"skip   {owner:<6} already funded ({account.balance_minor} minor units)")
                continue

            outcome = await payments.execute(
                PaymentCommand(
                    scope="seed",
                    idempotency_key=f"seed:{owner}:v1",
                    from_account_id=treasury_id,
                    to_account_id=account.id,
                    amount_minor=amount_minor,
                    currency="USD",
                    description="opening balance",
                )
            )
            print(
                f"funded {owner:<6} {amount_minor:>8} minor units  "
                f"txn={json.loads(outcome.body_json)['id']}"
            )

        async with sessionmaker() as session:
            rows = (
                await session.scalars(
                    select(Account).order_by(Account.owner_ref, Account.account_type)
                )
            ).all()
            print("\n--- balances ---")
            for row in rows:
                print(f"{row.owner_ref:<16} {row.account_type:<9} {row.balance_minor:>12}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
