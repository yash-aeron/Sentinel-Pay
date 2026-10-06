"""Decision policy: turn a score and rule floors into APPROVE/REVIEW/DECLINE.

The score is **not** a probability. It is a monotone transform of the model's
anomaly score, calibrated so that ordinary traffic sits low and the 99th
percentile of normal traffic lands at 0.5. See ``model.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Decision = Literal["APPROVE", "REVIEW", "DECLINE"]

BLOCK_FLOOR = 1.0


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    approve_max: float = 0.45
    review_max: float = 0.75

    def __post_init__(self) -> None:
        if not 0.0 <= self.approve_max < self.review_max <= 1.0:
            raise ValueError(
                "risk thresholds must satisfy 0 <= approve_max < review_max <= 1"
            )

    @property
    def review_floor(self) -> float:
        """Lowest score that is unambiguously inside the REVIEW band.

        Rules that demand review do not pretend to know a score; they lift the
        result to the middle of the band so no threshold tweak can turn them
        into an approval by accident.
        """
        return (self.approve_max + self.review_max) / 2.0

    def band(self, score: float) -> Decision:
        if score < self.approve_max:
            return "APPROVE"
        if score < self.review_max:
            return "REVIEW"
        return "DECLINE"


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    decision: Decision
    score: float
    model_score: float
    rule_floor: float
    rules_fired: tuple[str, ...]
    model_version: str
    degraded: bool
    features: dict[str, float] = field(default_factory=dict)
    scored_at: datetime | None = None


__all__ = ["BLOCK_FLOOR", "Decision", "RiskAssessment", "RiskPolicy"]
