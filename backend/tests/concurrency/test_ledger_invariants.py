"""The ledger invariant, checked against the database rather than the service."""

from __future__ import annotations

import asyncio
import random
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

pytestmark = pytest.mark.concurrency


async def test_ledger_sums_to_zero_after_random_traffic(
    client: AsyncClient, funded_pair: dict[str, str], engine
) -> None:
    # Fixed seed: the run must be reproducible. Not a security context.
    rng = random.Random(20261024)  # noqa: S311
    alice, bob = funded_pair["alice"], funded_pair["bob"]
    run = uuid.uuid4()

    async def send(index: int):
        source, destination = (alice, bob) if index % 2 == 0 else (bob, alice)
        return await client.post(
            "/transactions",
            json={
                "from_account_id": source,
                "to_account_id": destination,
                "amount_minor": rng.randint(1, 900),
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"rand-{run}-{index}"},
        )

    results = await asyncio.gather(*(send(i) for i in range(120)))
    assert all(r.status_code in (201, 409) for r in results)

    async with engine.connect() as conn:
        # 1. Every transaction balances on its own.
        per_transaction = (
            await conn.execute(
                text(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT transaction_id
                        FROM ledger_entries
                        GROUP BY transaction_id
                        HAVING SUM(CASE WHEN direction = 'credit'
                                        THEN amount_minor ELSE -amount_minor END) <> 0
                    ) offenders
                    """
                )
            )
        ).scalar_one()
        assert per_transaction == 0

        # 2. The whole ledger sums to zero.
        global_sum = await conn.scalar(
            text(
                """
                SELECT COALESCE(SUM(CASE WHEN direction = 'credit'
                                         THEN amount_minor ELSE -amount_minor END), 0)
                FROM ledger_entries
                """
            )
        )
        assert global_sum == 0

        # 3. Every account balance equals its own entry history.
        drifted = (
            await conn.execute(
                text(
                    """
                    SELECT a.id, a.balance_minor, COALESCE(SUM(
                        CASE WHEN e.direction = 'credit' THEN e.amount_minor
                             ELSE -e.amount_minor END), 0) AS derived
                    FROM accounts a
                    LEFT JOIN ledger_entries e ON e.account_id = a.id
                    GROUP BY a.id, a.balance_minor
                    HAVING a.balance_minor <> COALESCE(SUM(
                        CASE WHEN e.direction = 'credit' THEN e.amount_minor
                             ELSE -e.amount_minor END), 0)
                    """
                )
            )
        ).all()
        assert drifted == []

        # 4. No customer account went negative.
        negative = await conn.scalar(
            text(
                "SELECT COUNT(*) FROM accounts "
                "WHERE account_type = 'customer' AND balance_minor < 0"
            )
        )
        assert negative == 0

        # 5. Sanity: the run actually moved money.
        settled = await conn.scalar(
            text("SELECT COUNT(*) FROM transactions WHERE status = 'settled'")
        )
        assert settled > 2
