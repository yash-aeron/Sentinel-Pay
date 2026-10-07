"""Per-user sliding-window velocity features backed by Redis.

Every payment attempt appends itself to a sorted set before the windows are
measured, so the attempt being scored is included. That ordering is deliberate:
scoring a payment against only the past would let the first request of a card
testing burst look clean, and card testing is exactly the pattern of *many*
requests where each individual one is unremarkable.

The append happens even when the transfer later fails (insufficient funds, a
decline). An attempt is a signal about intent; a successful settlement is not
the only thing a fraud engine should see.

One Lua script per user gives atomic append-and-measure: no read-then-write
race can lose an event, and the whole thing is a single Redis round trip.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from app.core.errors import VelocityUnavailable

# Ordered oldest-window-last is irrelevant; features.py names the fields.
WINDOW_1M_MS = 60_000
WINDOW_10M_MS = 600_000
WINDOW_1H_MS = 3_600_000

# A user with more than this many attempts in an hour has already tripped the
# blocking velocity rule. Capping the set bounds the cost of the parse below
# without weakening any decision that is still reachable.
MAX_EVENTS_PER_USER = 1_000
TTL_SECONDS = 7_200
KEY_PREFIX = "sp:velocity"


@dataclass(frozen=True, slots=True)
class VelocitySnapshot:
    """Window aggregates, including the attempt currently being scored."""

    count_1m: int
    count_10m: int
    count_1h: int
    amount_sum_1m: int
    amount_sum_10m: int
    amount_sum_1h: int
    countries_1m: int
    countries_10m: int
    countries_1h: int
    merchants_1m: int
    merchants_10m: int
    merchants_1h: int
    is_new_merchant: bool


EMPTY_SNAPSHOT = VelocitySnapshot(
    count_1m=0,
    count_10m=0,
    count_1h=0,
    amount_sum_1m=0,
    amount_sum_10m=0,
    amount_sum_1h=0,
    countries_1m=0,
    countries_10m=0,
    countries_1h=0,
    merchants_1m=0,
    merchants_10m=0,
    merchants_1h=0,
    is_new_merchant=False,
)


# KEYS[1] = events ZSET, KEYS[2] = merchants SET
# ARGV = now_ms, member, amount, country, merchant ("" when absent), ttl, max
#
# Member encoding is ``transaction_id|amount_minor|country|merchant``. The
# country is a validated ISO-3166 alpha-2 code and the merchant id is validated
# to exclude ``|``, so the split is unambiguous.
_RECORD_AND_MEASURE = """
local zkey = KEYS[1]
local mkey = KEYS[2]
local now = tonumber(ARGV[1])
local member = ARGV[2]
local amount = tonumber(ARGV[3])
local country = ARGV[4]
local merchant = ARGV[5]
local ttl = tonumber(ARGV[6])
local max_events = tonumber(ARGV[7])

local is_new = 0
if merchant ~= '' then
    is_new = redis.call('SISMEMBER', mkey, merchant)
    redis.call('SADD', mkey, merchant)
    redis.call('EXPIRE', mkey, ttl)
end

redis.call('ZADD', zkey, now, member)
redis.call('ZREMRANGEBYSCORE', zkey, '-inf', now - 3600000)
local size = redis.call('ZCARD', zkey)
if size > max_events then
    redis.call('ZREMRANGEBYRANK', zkey, 0, size - max_events - 1)
end
redis.call('EXPIRE', zkey, ttl)

local function measure(window_ms)
    local members = redis.call('ZRANGEBYSCORE', zkey, now - window_ms, '+inf')
    local count = 0
    local total = 0
    local countries = {}
    local merchants = {}
    local country_count = 0
    local merchant_count = 0

    for i = 1, #members do
        local raw = members[i]
        local p1 = string.find(raw, '|', 1, true)
        if p1 then
            local p2 = string.find(raw, '|', p1 + 1, true)
            local p3 = string.find(raw, '|', p2 + 1, true)
            if p2 and p3 then
                count = count + 1
                total = total + (tonumber(string.sub(raw, p1 + 1, p2 - 1)) or 0)
                local c = string.sub(raw, p2 + 1, p3 - 1)
                local m = string.sub(raw, p3 + 1)
                if not countries[c] then
                    countries[c] = true
                    country_count = country_count + 1
                end
                if m ~= '' and not merchants[m] then
                    merchants[m] = true
                    merchant_count = merchant_count + 1
                end
            end
        end
    end

    return {count, total, country_count, merchant_count}
end

local one_minute = measure(60000)
local ten_minutes = measure(600000)
local one_hour = measure(3600000)

return {
    one_minute[1], one_minute[2], one_minute[3], one_minute[4],
    ten_minutes[1], ten_minutes[2], ten_minutes[3], ten_minutes[4],
    one_hour[1], one_hour[2], one_hour[3], one_hour[4],
    is_new
}
"""


class VelocityStore:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def ping(self) -> None:
        """Raise if Redis cannot answer. Used by the readiness probe."""
        await self._redis.ping()

    @staticmethod
    def _events_key(user_ref: str) -> str:
        return f"{KEY_PREFIX}:{user_ref}:events"

    @staticmethod
    def _merchants_key(user_ref: str) -> str:
        return f"{KEY_PREFIX}:{user_ref}:merchants"

    async def record_and_measure(
        self,
        *,
        user_ref: str,
        transaction_id: uuid.UUID,
        amount_minor: int,
        country: str,
        merchant_id: str | None,
        now_ms: int | None = None,
    ) -> VelocitySnapshot:
        """Append this attempt, then measure every window in one atomic step.

        Raises ``VelocityUnavailable`` when Redis cannot answer; the risk engine
        turns that into a REVIEW rather than guessing.
        """
        if "|" in user_ref:
            raise ValueError("user_ref must not contain '|'")
        if "|" in country:
            raise ValueError("country must not contain '|'")
        if merchant_id is not None and "|" in merchant_id:
            raise ValueError("merchant_id must not contain '|'")

        moment = int(time.time() * 1000) if now_ms is None else now_ms
        member = f"{transaction_id}|{amount_minor}|{country}|{merchant_id or ''}"

        try:
            raw: Any = await self._redis.eval(
                _RECORD_AND_MEASURE,
                2,
                self._events_key(user_ref),
                self._merchants_key(user_ref),
                str(moment),
                member,
                str(amount_minor),
                country,
                merchant_id or "",
                str(TTL_SECONDS),
                str(MAX_EVENTS_PER_USER),
            )
        except (RedisError, OSError) as exc:
            raise VelocityUnavailable(f"redis velocity store unavailable: {exc}") from exc

        values = [int(value) for value in raw]
        return VelocitySnapshot(
            count_1m=values[0],
            amount_sum_1m=values[1],
            countries_1m=values[2],
            merchants_1m=values[3],
            count_10m=values[4],
            amount_sum_10m=values[5],
            countries_10m=values[6],
            merchants_10m=values[7],
            count_1h=values[8],
            amount_sum_1h=values[9],
            countries_1h=values[10],
            merchants_1h=values[11],
            # The Lua script returns SISMEMBER's answer: 1 means the merchant
            # was seen before, so "is new" is its negation -- but only when a
            # merchant was sent at all (ARGV[5] empty leaves the flag at 0).
            is_new_merchant=merchant_id is not None and not values[12],
        )


__all__ = [
    "EMPTY_SNAPSHOT",
    "MAX_EVENTS_PER_USER",
    "VelocitySnapshot",
    "VelocityStore",
]
