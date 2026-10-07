"""Append-only decision audit in MongoDB.

Postgres remains the source of truth for money. This collection is the
forensic record: why each attempt was scored the way it was, including the
features and rules at the moment of the decision. It is keyed by transaction
id, so writing it is an idempotent upsert — the retry after a failed audit
cannot create a second copy.

The write happens *after* the Postgres commit. A payment that settled must not
be rolled back because an audit insert timed out; the client retries with the
same idempotency key, gets the stored response, and the upsert is retried.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

from app.core.errors import AuditUnavailable
from app.core.logging import get_logger
from app.db.models import Transaction

log = get_logger(__name__)

COLLECTION = "transaction_audit"
MAX_ATTEMPTS = 3


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def transaction_document(transaction: Transaction) -> dict[str, Any]:
    """Project a Postgres row into the audit document.

    Replays rebuild the document from the row rather than from the original
    in-memory assessment, so a replay after a crash still audits the payment.
    """
    return {
        "_id": str(transaction.id),
        "transaction_id": str(transaction.id),
        "user_ref": transaction.user_ref,
        "from_account_id": str(transaction.from_account_id),
        "to_account_id": str(transaction.to_account_id),
        "amount_minor": transaction.amount_minor,
        "currency": transaction.currency.strip(),
        "country": transaction.country,
        "merchant_id": transaction.merchant_id,
        "device_id": transaction.device_id,
        "status": transaction.status,
        "decision": transaction.decision,
        "risk_score": transaction.risk_score,
        "model_version": transaction.model_version,
        "rules_fired": list(transaction.rules_fired or []),
        "features": transaction.features,
        "velocity_degraded": transaction.velocity_degraded,
        "review_status": transaction.review_status,
        "scored_at": _iso(transaction.scored_at),
        "created_at": _iso(transaction.created_at),
    }


class AuditStore:
    def __init__(self, client: AsyncMongoClient, database: str) -> None:
        self._collection = client[database][COLLECTION]
        self._client = client

    async def ping(self) -> None:
        """Raise if MongoDB cannot answer. Used by the readiness probe."""
        await self._client.admin.command("ping")

    async def ensure_indexes(self) -> None:
        await self._collection.create_index([("user_ref", 1), ("created_at", -1)])
        await self._collection.create_index([("decision", 1), ("created_at", -1)])
        await self._collection.create_index([("review_status", 1)])
        await self._collection.create_index([("created_at", -1)])

    async def record(self, transaction: Transaction) -> None:
        await self.upsert(transaction_document(transaction))

    async def upsert(self, document: dict[str, Any]) -> None:
        last_error: PyMongoError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                await self._collection.replace_one(
                    {"_id": document["_id"]}, document, upsert=True
                )
                return
            except PyMongoError as exc:
                last_error = exc
                log.warning(
                    "audit_write_failed",
                    transaction_id=document["_id"],
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(0.05 * attempt)
        raise AuditUnavailable(
            "the payment was recorded but the audit log could not be written",
            transaction_id=document["_id"],
        ) from last_error

    async def get(self, transaction_id: str) -> dict[str, Any] | None:
        return await self._collection.find_one({"_id": transaction_id})

    async def count(self) -> int:
        return int(await self._collection.count_documents({}))


__all__ = ["AuditStore", "COLLECTION", "transaction_document"]
