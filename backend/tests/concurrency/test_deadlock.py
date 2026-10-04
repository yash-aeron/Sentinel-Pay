"""Opposing transfers must never deadlock.

A->B and B->A in the same instant is the classic two-row deadlock. It is
prevented by locking both accounts in ascending id order, one statement per
account, never by retrying after the fact.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.concurrency

PAIRS = 50
AMOUNT = 100


async def test_opposing_transfers_never_deadlock(
    client: AsyncClient, funded_pair: dict[str, str]
) -> None:
    run = uuid.uuid4()
    alice, bob = funded_pair["alice"], funded_pair["bob"]

    async def send(source: str, destination: str, label: str, index: int):
        return await client.post(
            "/transactions",
            json={
                "from_account_id": source,
                "to_account_id": destination,
                "amount_minor": AMOUNT,
                "currency": "USD",
                "description": label,
            },
            headers={"Idempotency-Key": f"{label}-{run}-{index}"},
        )

    tasks = []
    for index in range(PAIRS):
        tasks.append(send(alice, bob, "ab", index))
        tasks.append(send(bob, alice, "ba", index))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"transfers raised instead of settling: {errors[:3]}"
    assert all(r.status_code == 201 for r in results), [
        (r.status_code, r.text) for r in results if r.status_code != 201
    ]

    # Equal traffic both ways: both balances return to their starting point.
    alice_state = (await client.get(f"/accounts/{alice}")).json()
    bob_state = (await client.get(f"/accounts/{bob}")).json()
    assert alice_state["balance_minor"] == 100_000
    assert bob_state["balance_minor"] == 100_000


async def test_opposing_transfers_keep_the_ledger_balanced(
    client: AsyncClient, funded_pair: dict[str, str], engine
) -> None:
    from sqlalchemy import text

    run = uuid.uuid4()
    alice, bob = funded_pair["alice"], funded_pair["bob"]

    async def send(source: str, destination: str, label: str, index: int):
        return await client.post(
            "/transactions",
            json={
                "from_account_id": source,
                "to_account_id": destination,
                "amount_minor": AMOUNT + index,
                "currency": "USD",
            },
            headers={"Idempotency-Key": f"{label}-{run}-{index}"},
        )

    await asyncio.gather(
        *[
            send(alice, bob, "ab", i) if i % 2 == 0 else send(bob, alice, "ba", i)
            for i in range(20)
        ]
    )

    async with engine.connect() as conn:
        unbalanced = (
            await conn.execute(
                text(
                    """
                    SELECT transaction_id
                    FROM ledger_entries
                    GROUP BY transaction_id
                    HAVING SUM(CASE WHEN direction = 'credit'
                                    THEN amount_minor ELSE -amount_minor END) <> 0
                    """
                )
            )
        ).all()
    assert unbalanced == []
