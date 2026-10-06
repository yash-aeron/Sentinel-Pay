"""The single feature contract shared by training and serving.

The ordering of ``FEATURE_NAMES`` is part of the model artifact contract. A
model trained on a different order or a different set of columns would produce
confident nonsense, so the loader refuses to serve an artifact whose names do
not match this tuple exactly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from app.services.velocity import VelocitySnapshot

FEATURE_NAMES: tuple[str, ...] = (
    "log_amount",
    "count_1m",
    "count_10m",
    "count_1h",
    "log_amount_sum_1m",
    "log_amount_sum_10m",
    "log_amount_sum_1h",
    "countries_1m",
    "countries_10m",
    "countries_1h",
    "merchants_1m",
    "merchants_10m",
    "merchants_1h",
    "cross_border",
    "new_merchant",
    "hour_of_day",
    "log_amount_vs_prior_avg_10m",
)

FEATURE_COUNT = len(FEATURE_NAMES)


@dataclass(frozen=True, slots=True)
class FeatureInputs:
    amount_minor: int
    snapshot: VelocitySnapshot
    home_country: str
    country: str
    merchant_id: str | None
    hour_of_day: int


def _log_minor(amount_minor: int) -> float:
    # Minor units are cents; scaling to dollars before the log keeps the value
    # in a range where a tree split is meaningful.
    return math.log1p(max(amount_minor, 0) / 100.0)


def _amount_vs_prior_avg(amount_minor: int, snapshot: VelocitySnapshot) -> float:
    """log of the current amount over the 10m window's *prior* average.

    The snapshot includes this attempt, so the prior window is the snapshot
    minus it. The ratio — not the absolute amount — is what marks a cash-out:
    a large payment from an account whose recent history is large payments is
    ordinary, and only the contrast with history is anomalous. 0.0 when there
    is no prior history to compare against.
    """
    prior_count = snapshot.count_10m - 1
    prior_sum = snapshot.amount_sum_10m - amount_minor
    if prior_count < 1 or prior_sum <= 0:
        return 0.0
    ratio = amount_minor / (prior_sum / prior_count)
    return math.log(max(ratio, 0.01))


def build_features(inputs: FeatureInputs) -> dict[str, float]:
    """Build features in contract order. Used by both the trainer and the API."""
    snapshot = inputs.snapshot
    values = (
        _log_minor(inputs.amount_minor),
        float(snapshot.count_1m),
        float(snapshot.count_10m),
        float(snapshot.count_1h),
        _log_minor(snapshot.amount_sum_1m),
        _log_minor(snapshot.amount_sum_10m),
        _log_minor(snapshot.amount_sum_1h),
        float(snapshot.countries_1m),
        float(snapshot.countries_10m),
        float(snapshot.countries_1h),
        float(snapshot.merchants_1m),
        float(snapshot.merchants_10m),
        float(snapshot.merchants_1h),
        1.0 if inputs.country != inputs.home_country else 0.0,
        1.0 if inputs.merchant_id is not None and snapshot.is_new_merchant else 0.0,
        float(inputs.hour_of_day),
        _amount_vs_prior_avg(inputs.amount_minor, snapshot),
    )
    return dict(zip(FEATURE_NAMES, values, strict=True))


def to_vector(features: dict[str, float]) -> list[float]:
    return [features[name] for name in FEATURE_NAMES]


__all__ = ["FEATURE_COUNT", "FEATURE_NAMES", "FeatureInputs", "build_features", "to_vector"]
