"""Velocity windows against real Redis: the Lua script's exact semantics.

All timestamps are explicit (``now_ms``) so the tests never depend on wall
clock pacing; the requests themselves are instant.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.velocity import VelocitySnapshot, VelocityStore

pytestmark = pytest.mark.risk

T0 = 1_700_000_000_000


def store(velocity_redis) -> VelocityStore:
    return VelocityStore(velocity_redis)


async def record(
    velocity: VelocityStore,
    user: str = "alice",
    *,
    amount: int = 1_000,
    country: str = "US",
    merchant: str | None = None,
    now_ms: int = T0,
) -> VelocitySnapshot:
    return await velocity.record_and_measure(
        user_ref=user,
        transaction_id=uuid.uuid4(),
        amount_minor=amount,
        country=country,
        merchant_id=merchant,
        now_ms=now_ms,
    )


async def test_first_attempt_measures_only_itself(velocity_redis):
    snapshot = await store(velocity_redis).record_and_measure(
        user_ref="alice",
        transaction_id=uuid.uuid4(),
        amount_minor=2_500,
        country="US",
        merchant_id="m-1",
        now_ms=T0,
    )
    assert snapshot.count_1m == 1
    assert snapshot.count_10m == 1
    assert snapshot.count_1h == 1
    assert snapshot.amount_sum_1m == 2_500
    assert snapshot.countries_1m == 1
    assert snapshot.merchants_1m == 1
    assert snapshot.is_new_merchant is True


async def test_windows_accumulate_and_distinct_counts_are_tracked(velocity_redis):
    velocity = store(velocity_redis)
    await record(velocity, amount=1_000, country="US", now_ms=T0)
    await record(velocity, amount=2_000, country="FR", merchant="m-1", now_ms=T0 + 30_000)
    snapshot = await record(velocity, amount=3_000, country="FR", now_ms=T0 + 45_000)
    assert snapshot.count_1m == 3
    assert snapshot.amount_sum_1m == 6_000
    assert snapshot.countries_1m == 2
    assert snapshot.merchants_1m == 1  # m-1 seen before; the other had no merchant
    assert snapshot.is_new_merchant is False


async def test_one_minute_window_excludes_events_older_than_sixty_seconds(velocity_redis):
    velocity = store(velocity_redis)
    await record(velocity, amount=1_000, now_ms=T0)
    await record(velocity, amount=2_000, now_ms=T0 + 30_000)
    snapshot = await record(velocity, amount=4_000, now_ms=T0 + 61_000)
    # T0 fell out of the 1m window (61s old); 30s and current remain.
    assert snapshot.count_1m == 2
    assert snapshot.amount_sum_1m == 6_000
    # ...but everything is still inside 10m and 1h.
    assert snapshot.count_10m == 3
    assert snapshot.count_1h == 3


async def test_events_are_isolated_per_user(velocity_redis):
    velocity = store(velocity_redis)
    await record(velocity, user="alice", amount=1_000, now_ms=T0)
    snapshot = await record(velocity, user="bob", amount=5_000, now_ms=T0 + 1_000)
    assert snapshot.count_1m == 1
    assert snapshot.amount_sum_1m == 5_000


async def test_merchant_seen_in_an_earlier_hour_is_not_new(velocity_redis):
    velocity = store(velocity_redis)
    first = await velocity.record_and_measure(
        user_ref="alice", transaction_id=uuid.uuid4(), amount_minor=100,
        country="US", merchant_id="m-9", now_ms=T0,
    )
    again = await velocity.record_and_measure(
        user_ref="alice", transaction_id=uuid.uuid4(), amount_minor=100,
        country="US", merchant_id="m-9", now_ms=T0 + 3_600_001,
    )
    assert first.is_new_merchant is True
    # The events set expired out of the window, but merchant memory persists.
    assert again.count_1m == 1
    assert again.is_new_merchant is False


async def test_member_fields_are_pipe_safe_by_validation(velocity_redis):
    velocity = store(velocity_redis)
    for bad in ("a|b",):
        with pytest.raises(ValueError, match="user_ref"):
            await velocity.record_and_measure(
                user_ref=bad, transaction_id=uuid.uuid4(), amount_minor=1,
                country="US", merchant_id=None, now_ms=T0,
            )
    with pytest.raises(ValueError, match="merchant_id"):
        await velocity.record_and_measure(
            user_ref="alice", transaction_id=uuid.uuid4(), amount_minor=1,
            country="US", merchant_id="x|y", now_ms=T0,
        )
