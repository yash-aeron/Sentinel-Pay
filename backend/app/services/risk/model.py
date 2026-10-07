"""Anomaly model and its calibration.

``IsolationForest`` produces an unbounded anomaly score whose absolute value
means nothing to a threshold. The calibration maps it onto [0, 1] with a
monotone logistic anchored on two quantiles of *normal* training traffic:

    t = (raw - q50) / (q99 - q50)
    score = 1 / (1 + exp(-k * (t - 1)))

so the median of normal traffic maps to ~0.018 and the 99th percentile maps to
exactly 0.5. With ``k=4`` the curve is steep around q99: scores above it move
into the decline band quickly. This is a ranking device, not a probability.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from app.core.logging import get_logger
from app.services.risk.features import FEATURE_NAMES, to_vector

log = get_logger(__name__)

MODEL_VERSION = "iforest-v1"
RULES_ONLY_VERSION = "rules-only"


class RiskModel(Protocol):
    version: str

    def score(self, features: Mapping[str, float]) -> float: ...


class RulesOnlyModel:
    """Fallback when no artifact exists. Score is zero, so rules decide."""

    version = RULES_ONLY_VERSION

    def score(self, features: Mapping[str, float]) -> float:
        return 0.0


@dataclass(frozen=True, slots=True)
class Calibration:
    q50: float
    q99: float
    k: float = 4.0

    def __post_init__(self) -> None:
        if self.q99 <= self.q50:
            raise ValueError("calibration q99 must be greater than q50")
        if self.k <= 0:
            raise ValueError("calibration k must be positive")

    def apply(self, raw: float) -> float:
        t = (raw - self.q50) / (self.q99 - self.q50)
        # Clamp before exp: the logistic saturates long before these bounds, so
        # this only prevents overflow on absurd inputs.
        t = max(-60.0, min(60.0, t))
        return 1.0 / (1.0 + math.exp(-self.k * (t - 1.0)))


class IsolationForestModel:
    def __init__(
        self,
        estimator: IsolationForest,
        calibration: Calibration,
        version: str = MODEL_VERSION,
    ) -> None:
        self._estimator = estimator
        self._calibration = calibration
        self.version = version

    def score(self, features: Mapping[str, float]) -> float:
        # ``to_vector`` enforces the shared column order.
        vector = np.asarray([to_vector(dict(features))], dtype=float)
        raw = float(-self._estimator.score_samples(vector)[0])
        return self._calibration.apply(raw)


def load_risk_model(model_file: Path, meta_file: Path) -> RiskModel:
    """Load the artifact, or fall back to rules-only when it is absent.

    A missing artifact is a documented setup state (run ``python -m ml.train``).
    A *mismatched* artifact is a bug: serving it would silently mis-score every
    payment, so it raises instead of degrading.
    """
    if not model_file.exists() or not meta_file.exists():
        log.warning(
            "risk_model_missing",
            model_file=str(model_file),
            meta_file=str(meta_file),
            fallback=RULES_ONLY_VERSION,
        )
        return RulesOnlyModel()

    payload: dict[str, Any] = joblib.load(model_file)
    meta: dict[str, Any] = json.loads(meta_file.read_text(encoding="utf-8"))

    payload_names = list(payload.get("feature_names", []))
    meta_names = list(meta.get("feature_names", []))
    expected = list(FEATURE_NAMES)
    if payload_names != expected or meta_names != expected:
        raise ValueError(
            "model feature contract mismatch: "
            f"artifact={payload_names} meta={meta_names} expected={expected}"
        )

    if payload.get("version") != meta.get("version"):
        raise ValueError("model and metadata disagree about the version")

    calibration_raw = payload.get("calibration") or {}
    calibration = Calibration(
        q50=float(calibration_raw["q50"]),
        q99=float(calibration_raw["q99"]),
        k=float(calibration_raw.get("k", 4.0)),
    )

    estimator = payload["estimator"]
    if not isinstance(estimator, IsolationForest):
        raise TypeError("model artifact does not contain an IsolationForest")

    log.info(
        "risk_model_loaded",
        version=payload["version"],
        q50=calibration.q50,
        q99=calibration.q99,
    )
    return IsolationForestModel(
        estimator=estimator, calibration=calibration, version=str(payload["version"])
    )


__all__ = [
    "Calibration",
    "IsolationForestModel",
    "MODEL_VERSION",
    "RULES_ONLY_VERSION",
    "RiskModel",
    "RulesOnlyModel",
    "load_risk_model",
]
