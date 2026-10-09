"""Risk engine composition: velocity -> features -> rules + model -> decision.

Uses fakes for the velocity store and the model so the composition logic is
tested without Redis or an artifact. The store against real Redis and the
payments flow end to end are covered in ``tests/risk``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.core.errors import VelocityUnavailable
from app.services.risk.decision import RiskPolicy
from app.services.risk.engine import (
    VELOCITY_UNAVAILABLE_RULE,
    AssessmentRequest,
    RiskEngine,
)
from app.services.risk.features import FEATURE_NAMES
from app.services.velocity import EMPTY_SNAPSHOT, VelocitySnapshot


class StubVelocity:
    def __init__(self, snapshot: VelocitySnapshot | None = None, *, down: bool = False):
        self._snapshot = snapshot or EMPTY_SNAPSHOT
        self._down = down

    async def record_and_measure(self, **_: object) -> VelocitySnapshot:
        if self._down:
            raise VelocityUnavailable("redis unreachable (simulated)")
        return self._snapshot


class StubModel:
    version = "stub-model-1"

    def __init__(self, score: float = 0.0):
        self._score = score

    def score(self, features: object) -> float:
        return self._score


def request() -> AssessmentRequest:
    return AssessmentRequest(
        transaction_id=uuid.uuid4(),
        user_ref="alice",
        amount_minor=2_000,
        home_country="US",
        country="US",
        merchant_id=None,
        occurred_at=datetime(2026, 10, 24, 12, 0, tzinfo=UTC),
    )


def make_engine(
    snapshot: VelocitySnapshot | None = None,
    *,
    down: bool = False,
    model: float = 0.0,
    policy: RiskPolicy | None = None,
) -> RiskEngine:
    return RiskEngine(StubVelocity(snapshot, down=down), StubModel(model), policy)


def quiet_snapshot(**overrides: int) -> VelocitySnapshot:
    base = {
        "count_1m": 1, "count_10m": 1, "count_1h": 1,
        "amount_sum_1m": 2_000, "amount_sum_10m": 2_000, "amount_sum_1h": 2_000,
        "countries_1m": 1, "countries_10m": 1, "countries_1h": 1,
        "merchants_1m": 0, "merchants_10m": 0, "merchants_1h": 0,
        "is_new_merchant": False,
    }
    return VelocitySnapshot(**base | overrides)


async def test_low_score_clean_history_approves():
    assessment = await make_engine(quiet_snapshot(), model=0.1).assess(request())
    assert assessment.decision == "APPROVE"
    assert assessment.score == 0.1
    assert assessment.degraded is False
    assert assessment.rules_fired == ()
    assert tuple(assessment.features) == FEATURE_NAMES


@pytest.mark.parametrize(
    ("model_score", "expected"),
    [(0.44, "APPROVE"), (0.45, "REVIEW"), (0.74, "REVIEW"), (0.75, "DECLINE")],
)
async def test_model_score_alone_selects_the_band(model_score: float, expected: str):
    assessment = await make_engine(quiet_snapshot(), model=model_score).assess(request())
    assert assessment.decision == expected


async def test_block_rule_overrides_a_calm_model():
    snapshot = quiet_snapshot(count_10m=20)  # R_VELOCITY_20_10M -> block
    assessment = await make_engine(snapshot, model=0.0).assess(request())
    assert assessment.decision == "DECLINE"
    assert assessment.score == 1.0  # BLOCK_FLOOR
    assert assessment.rule_floor == 1.0
    assert "R_VELOCITY_20_10M" in assessment.rules_fired


async def test_review_rule_lifts_a_calm_model_to_review():
    snapshot = quiet_snapshot(countries_1m=3)  # geo hop -> review
    assessment = await make_engine(snapshot, model=0.05).assess(request())
    policy = RiskPolicy()
    assert assessment.decision == "REVIEW"
    assert assessment.score == policy.review_floor
    assert assessment.model_score == 0.05


async def test_decision_is_the_max_of_model_and_rule_floor():
    # Model already in DECLINE territory, rule asks for review: decline wins.
    snapshot = quiet_snapshot(countries_1m=3)
    assessment = await make_engine(snapshot, model=0.95).assess(request())
    assert assessment.decision == "DECLINE"
    assert assessment.score == 0.95


async def test_velocity_outage_holds_for_review_and_records_the_outage():
    assessment = await make_engine(down=True, model=0.0).assess(request())
    policy = RiskPolicy()
    assert assessment.decision == "REVIEW"
    assert assessment.degraded is True
    assert assessment.score == policy.review_floor
    assert assessment.model_score == 0.0
    assert assessment.rules_fired == (VELOCITY_UNAVAILABLE_RULE,)
    assert assessment.features["count_1h"] == 0.0  # built from the empty snapshot


async def test_custom_thresholds_flow_through():
    strict = RiskPolicy(approve_max=0.1, review_max=0.2)
    assessment = await make_engine(quiet_snapshot(), model=0.15, policy=strict).assess(request())
    assert assessment.decision == "REVIEW"


def test_model_version_is_surfaced():
    assert make_engine().model_version == "stub-model-1"
