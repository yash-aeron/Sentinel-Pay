"""Risk engine: velocity -> features -> rules + model -> decision."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.core.errors import VelocityUnavailable
from app.core.time import utcnow
from app.services.risk import rules
from app.services.risk.decision import BLOCK_FLOOR, RiskAssessment, RiskPolicy
from app.services.risk.features import FeatureInputs, build_features
from app.services.risk.model import RiskModel
from app.services.velocity import EMPTY_SNAPSHOT, VelocityStore

VELOCITY_UNAVAILABLE_RULE = "R_VELOCITY_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class AssessmentRequest:
    transaction_id: uuid.UUID
    user_ref: str
    amount_minor: int
    home_country: str
    country: str
    merchant_id: str | None
    occurred_at: datetime


class RiskEngine:
    def __init__(
        self,
        velocity: VelocityStore,
        model: RiskModel,
        policy: RiskPolicy | None = None,
    ) -> None:
        self._velocity = velocity
        self._model = model
        self._policy = policy or RiskPolicy()

    @property
    def model_version(self) -> str:
        return self._model.version

    async def assess(self, request: AssessmentRequest) -> RiskAssessment:
        degraded = False
        try:
            snapshot = await self._velocity.record_and_measure(
                user_ref=request.user_ref,
                transaction_id=request.transaction_id,
                amount_minor=request.amount_minor,
                country=request.country,
                merchant_id=request.merchant_id,
                now_ms=int(request.occurred_at.timestamp() * 1000),
            )
        except VelocityUnavailable:
            # Never fail open. A payment we cannot evaluate is held for a human,
            # and the absence of velocity data is itself recorded.
            snapshot = EMPTY_SNAPSHOT
            degraded = True

        features = build_features(
            FeatureInputs(
                amount_minor=request.amount_minor,
                snapshot=snapshot,
                home_country=request.home_country,
                country=request.country,
                merchant_id=request.merchant_id,
                hour_of_day=request.occurred_at.astimezone().hour,
            )
        )
        scored_at = utcnow()

        if degraded:
            return RiskAssessment(
                decision="REVIEW",
                score=self._policy.review_floor,
                model_score=0.0,
                rule_floor=self._policy.review_floor,
                rules_fired=(VELOCITY_UNAVAILABLE_RULE,),
                model_version=self._model.version,
                degraded=True,
                features=features,
                scored_at=scored_at,
            )

        result = rules.evaluate(
            rules.RuleInputs(
                amount_minor=request.amount_minor,
                snapshot=snapshot,
                home_country=request.home_country,
                country=request.country,
                merchant_id=request.merchant_id,
            )
        )

        if result.has_block:
            rule_floor = BLOCK_FLOOR
        elif result.needs_review:
            rule_floor = self._policy.review_floor
        else:
            rule_floor = 0.0

        model_score = self._model.score(features)
        score = max(model_score, rule_floor)
        return RiskAssessment(
            decision=self._policy.band(score),
            score=score,
            model_score=model_score,
            rule_floor=rule_floor,
            rules_fired=result.codes,
            model_version=self._model.version,
            degraded=False,
            features=features,
            scored_at=scored_at,
        )


__all__ = ["AssessmentRequest", "RiskEngine", "VELOCITY_UNAVAILABLE_RULE"]
