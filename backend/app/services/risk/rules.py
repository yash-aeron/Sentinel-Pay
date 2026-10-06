"""Deterministic rules layered over the model score.

The rules exist for two reasons:

1. Some fraud shapes are unambiguous and should not depend on a model's mood.
   Twenty payments in ten minutes is not a probability; it is a fact.
2. When the model is missing or Redis is down, the rules still say something
   useful about a request instead of failing open.

Each rule has an *action*, not a score. ``block`` means the decision is DECLINE
regardless of the model; ``review`` means at least REVIEW. The decision layer
turns those actions into floors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.services.velocity import VelocitySnapshot

RuleAction = Literal["block", "review"]

CARD_TESTING_MIN_COUNT_1M = 6
CARD_TESTING_MAX_AVERAGE_MINOR = 300
GEO_HOP_MIN_COUNTRIES_1M = 3
VELOCITY_BLOCK_COUNT_10M = 20
VELOCITY_REVIEW_COUNT_1H = 40
MERCHANT_SPRAY_MIN_COUNT_10M = 8
SPIKE_MULTIPLIER = 8
SPIKE_MIN_PRIOR_EVENTS = 3
CROSS_BORDER_LARGE_MINOR = 100_000


@dataclass(frozen=True, slots=True)
class RuleHit:
    code: str
    action: RuleAction
    detail: str


@dataclass(frozen=True, slots=True)
class RuleResult:
    hits: tuple[RuleHit, ...]

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(hit.code for hit in self.hits)

    @property
    def has_block(self) -> bool:
        return any(hit.action == "block" for hit in self.hits)

    @property
    def needs_review(self) -> bool:
        return bool(self.hits)


@dataclass(frozen=True, slots=True)
class RuleInputs:
    amount_minor: int
    snapshot: VelocitySnapshot
    home_country: str
    country: str
    merchant_id: str | None


def evaluate(inputs: RuleInputs) -> RuleResult:
    snapshot = inputs.snapshot
    hits: list[RuleHit] = []

    # Card testing: a burst of small charges. Velocity alone is not enough to
    # distinguish this from a busy merchant, so the average amount matters.
    if (
        snapshot.count_1m >= CARD_TESTING_MIN_COUNT_1M
        and snapshot.amount_sum_1m
        <= CARD_TESTING_MAX_AVERAGE_MINOR * snapshot.count_1m
    ):
        hits.append(
            RuleHit(
                code="R_CARD_TESTING",
                action="block",
                detail=(
                    f"{snapshot.count_1m} attempts in 60s averaging "
                    f"{snapshot.amount_sum_1m / snapshot.count_1m:.0f} minor units"
                ),
            )
        )

    if snapshot.countries_1m >= GEO_HOP_MIN_COUNTRIES_1M:
        hits.append(
            RuleHit(
                code="R_GEO_HOP_3_COUNTRIES_60S",
                action="review",
                detail=f"{snapshot.countries_1m} distinct countries in 60s",
            )
        )

    if snapshot.count_10m >= VELOCITY_BLOCK_COUNT_10M:
        hits.append(
            RuleHit(
                code="R_VELOCITY_20_10M",
                action="block",
                detail=f"{snapshot.count_10m} attempts in 10 minutes",
            )
        )

    if snapshot.count_1h >= VELOCITY_REVIEW_COUNT_1H:
        hits.append(
            RuleHit(
                code="R_VELOCITY_40_1H",
                action="review",
                detail=f"{snapshot.count_1h} attempts in one hour",
            )
        )

    if snapshot.merchants_10m >= MERCHANT_SPRAY_MIN_COUNT_10M:
        hits.append(
            RuleHit(
                code="R_MERCHANT_SPRAY_8_10M",
                action="review",
                detail=f"{snapshot.merchants_10m} distinct merchants in 10 minutes",
            )
        )

    # Compare the current amount with the average of the *prior* attempts in
    # the window. The snapshot includes this attempt, so subtract it first.
    prior_count = snapshot.count_10m - 1
    prior_sum = snapshot.amount_sum_10m - inputs.amount_minor
    if (
        prior_count >= SPIKE_MIN_PRIOR_EVENTS
        and prior_sum > 0
        and inputs.amount_minor * prior_count >= SPIKE_MULTIPLIER * prior_sum
    ):
        hits.append(
            RuleHit(
                code="R_AMOUNT_SPIKE_8X",
                action="review",
                detail=(
                    f"{inputs.amount_minor} is at least {SPIKE_MULTIPLIER}x the "
                    f"prior average of {prior_sum / prior_count:.0f}"
                ),
            )
        )

    if (
        inputs.country != inputs.home_country
        and inputs.amount_minor >= CROSS_BORDER_LARGE_MINOR
    ):
        hits.append(
            RuleHit(
                code="R_CROSS_BORDER_LARGE",
                action="review",
                detail=(
                    f"{inputs.amount_minor} minor units from {inputs.country} "
                    f"while home is {inputs.home_country}"
                ),
            )
        )

    return RuleResult(hits=tuple(hits))


__all__ = [
    "CROSS_BORDER_LARGE_MINOR",
    "RuleHit",
    "RuleInputs",
    "RuleResult",
    "evaluate",
]
