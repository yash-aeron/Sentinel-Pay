"""Audit store retry semantics, using a stub collection (no Mongo needed)."""

from __future__ import annotations

from typing import Any

import pytest
from pymongo.errors import PyMongoError

from app.core.errors import AuditUnavailable
from app.services.audit import MAX_ATTEMPTS, AuditStore


class FlakyCollection:
    """Fails ``replace_one`` the first ``fail_times`` calls, then succeeds."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self.written: list[dict[str, Any]] = []

    async def replace_one(self, filter: dict, document: dict, upsert: bool = False) -> int:  # noqa: A002
        assert upsert is True
        self.calls += 1
        if self.calls <= self.fail_times:
            raise PyMongoError(f"simulated failure #{self.calls}")
        self.written.append(document)
        return 1


class StubClient:
    def __init__(self, collection: FlakyCollection) -> None:
        self._collection = collection

    def __getitem__(self, database: str) -> dict[str, FlakyCollection]:
        return {"transaction_audit": self._collection}


def doc(transaction_id: str = "t-1") -> dict[str, Any]:
    return {"_id": transaction_id, "status": "settled"}


async def test_upsert_retries_transient_failures():
    collection = FlakyCollection(fail_times=2)
    store = AuditStore(StubClient(collection), database="any")  # type: ignore[arg-type]
    # Backoff sleeps are 0.05s + 0.1s; real, to keep the timing honest.
    await store.upsert(doc())
    assert collection.calls == 3
    assert collection.written[0]["_id"] == "t-1"


async def test_upsert_gives_up_after_max_attempts():
    collection = FlakyCollection(fail_times=MAX_ATTEMPTS)
    store = AuditStore(StubClient(collection), database="any")  # type: ignore[arg-type]
    with pytest.raises(AuditUnavailable, match="could not be written") as exc_info:
        await store.upsert(doc())
    assert collection.calls == MAX_ATTEMPTS
    assert exc_info.value.context["transaction_id"] == "t-1"


@pytest.mark.asyncio
async def test_record_projects_the_row_and_upserts():
    class Transaction:
        id = "t-row"  # type: ignore[assignment]
        user_ref = "alice"
        from_account_id = "a"  # type: ignore[assignment]
        to_account_id = "b"  # type: ignore[assignment]
        amount_minor = 500
        currency = "USD "
        country = "US"
        merchant_id = None
        device_id = None
        status = "held"
        decision = "REVIEW"
        risk_score = 0.6
        model_version = "iforest-v1"
        rules_fired = ["R_GEO_HOP_3_COUNTRIES_60S"]
        features = {"count_1m": 3.0}
        velocity_degraded = False
        review_status = "pending"
        scored_at = None
        created_at = None

    collection = FlakyCollection(fail_times=0)
    store = AuditStore(StubClient(collection), database="any")  # type: ignore[arg-type]
    await store.record(Transaction())  # type: ignore[arg-type]
    written = collection.written[0]
    assert written["_id"] == "t-row"
    assert written["currency"] == "USD"  # CHAR padding stripped on projection
    assert written["rules_fired"] == ["R_GEO_HOP_3_COUNTRIES_60S"]
    assert written["review_status"] == "pending"
