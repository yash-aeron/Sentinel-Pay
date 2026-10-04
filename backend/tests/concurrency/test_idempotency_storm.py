"""The core correctness claim: duplicate submissions move money exactly once."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.concurrency

DUPLICATES = 50


def _body(source: str, destination: str, amount: int = 2_500) -> dict:
    return {
        "from_account_id": source,
        "to_account_id": destination,
        "amount_minor": amount,
        "currency": "USD",
        "description": "duplicate storm",
    }


async def test_fifty_concurrent_duplicates_move_money_once(
    client: AsyncClient, funded_pair: dict[str, str]
) -> None:
    key = f"storm-{uuid.uuid4()}"
    body = _body(funded_pair["alice"], funded_pair["bob"])
    headers = {"Idempotency-Key": key}

    responses = await asyncio.gather(
        *(client.post("/transactions", json=body, headers=headers) for _ in range(DUPLICATES))
    )

    assert {r.status_code for r in responses} == {201}, [
        (r.status_code, r.text) for r in responses if r.status_code != 201
    ]

    # Every caller sees byte-identical content, and it is the same transaction.
    # Byte equality matters: JSONB would silently reorder keys on replay.
    assert len({r.text for r in responses}) == 1
    assert len({r.json()["id"] for r in responses}) == 1

    replays = [r for r in responses if r.headers.get("Idempotent-Replay") == "true"]
    assert len(replays) == DUPLICATES - 1

    destination = await client.get(f"/accounts/{funded_pair['bob']}")
    source = await client.get(f"/accounts/{funded_pair['alice']}")
    assert destination.json()["balance_minor"] == 100_000 + 2_500
    assert source.json()["balance_minor"] == 100_000 - 2_500

    transaction_id = responses[0].json()["id"]
    detail = await client.get(f"/transactions/{transaction_id}")
    assert detail.status_code == 200
    assert len(detail.json()["entries"]) == 2


async def test_duplicate_movements_are_not_written_to_the_ledger(
    client: AsyncClient, funded_pair: dict[str, str], engine
) -> None:
    from sqlalchemy import func, select

    from app.db.models import LedgerEntry, Transaction

    body = _body(funded_pair["alice"], funded_pair["bob"], amount=1_000)
    headers = {"Idempotency-Key": f"storm-{uuid.uuid4()}"}

    await asyncio.gather(
        *(client.post("/transactions", json=body, headers=headers) for _ in range(DUPLICATES))
    )

    async with engine.connect() as conn:
        # 2 seed transfers from the fixture + exactly 1 storm transfer.
        total = await conn.scalar(select(func.count()).select_from(Transaction))
        entries = await conn.scalar(select(func.count()).select_from(LedgerEntry))
    assert total == 3
    assert entries == 6


async def test_same_key_different_body_is_rejected(
    client: AsyncClient, funded_pair: dict[str, str]
) -> None:
    key = f"reuse-{uuid.uuid4()}"
    headers = {"Idempotency-Key": key}

    first = await client.post(
        "/transactions", json=_body(funded_pair["alice"], funded_pair["bob"], 500), headers=headers
    )
    assert first.status_code == 201

    second = await client.post(
        "/transactions", json=_body(funded_pair["alice"], funded_pair["bob"], 900), headers=headers
    )
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "idempotency_key_reuse"

    # Nothing moved on the second attempt.
    source = await client.get(f"/accounts/{funded_pair['alice']}")
    assert source.json()["balance_minor"] == 100_000 - 500


async def test_missing_idempotency_key_is_rejected(
    client: AsyncClient, funded_pair: dict[str, str]
) -> None:
    response = await client.post(
        "/transactions", json=_body(funded_pair["alice"], funded_pair["bob"])
    )
    assert response.status_code == 422


async def test_insufficient_funds_does_not_touch_the_ledger(
    client: AsyncClient, funded_pair: dict[str, str], engine
) -> None:
    from sqlalchemy import func, select

    from app.db.models import LedgerEntry

    response = await client.post(
        "/transactions",
        json=_body(funded_pair["alice"], funded_pair["bob"], 500_000),
        headers={"Idempotency-Key": f"nf-{uuid.uuid4()}"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "insufficient_funds"

    async with engine.connect() as conn:
        entries = await conn.scalar(select(func.count()).select_from(LedgerEntry))
    assert entries == 4  # only the two seed transfers


async def test_database_refuses_an_overdraft_even_without_the_service(
    client: AsyncClient, funded_pair: dict[str, str], engine
) -> None:
    """The CHECK constraint is a real backstop, not decoration."""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE accounts SET balance_minor = -1 WHERE id = :id"),
                {"id": funded_pair["alice"]},
            )
