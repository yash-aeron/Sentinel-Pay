from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from sqlalchemy import BigInteger, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utcnow
from app.db.models import IdempotencyKey

RETENTION = timedelta(hours=24)


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 over canonical JSON.

    Sorted keys and a compact separator remove every ambiguity about "is this
    the same body" that would otherwise turn into a double charge.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def acquire_key_lock(session: AsyncSession, *, scope: str, key: str) -> None:
    """Serialise every request carrying the same scope:key.

    Transaction-scoped, so a crash or rollback releases it with no sweeper.
    Concurrent duplicates queue here and then hit the replay path, instead of
    racing into a unique-constraint violation.
    """
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"{scope}:{key}", literal(0, BigInteger))
            )
        )
    )


async def find_claim(session: AsyncSession, *, scope: str, key: str) -> IdempotencyKey | None:
    return await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.scope == scope, IdempotencyKey.key == key
        )
    )


def new_claim(*, scope: str, key: str, request_hash: str) -> IdempotencyKey:
    return IdempotencyKey(
        scope=scope,
        key=key,
        request_hash=request_hash,
        status="in_progress",
        expires_at=utcnow() + RETENTION,
    )
