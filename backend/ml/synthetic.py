"""Synthetic traffic for training and calibrating the anomaly model.

Every row is built through the same ``build_features`` the API uses at serving
time, from a ``VelocitySnapshot`` that a user exhibiting that behaviour would
produce. Training on anything else would teach the model a feature
distribution the serving path can never emit.

Four families, three of them fraud shapes the rules layer also knows about.
The model is *not* trained to identify the families — it never sees a label.
The labels exist so the trainer can report how well the unsupervised model
separates each shape from normal traffic, which is the only honest way to
sanity-check an anomaly detector.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np

from app.services.risk.features import FEATURE_NAMES, FeatureInputs, build_features
from app.services.velocity import VelocitySnapshot

HOME_COUNTRY = "US"


@dataclass(frozen=True, slots=True)
class Sample:
    features: dict[str, float]
    label: str


def _snapshot(
    *,
    amount_minor: int,
    prior_unit: int,
    count_1m: int,
    count_10m: int,
    count_1h: int,
    countries: int = 1,
    merchants: int = 0,
) -> VelocitySnapshot:
    """A window snapshot whose sums include the current attempt at its own size.

    Serving-time snapshots always contain the attempt being scored, so the
    training snapshots must too — a spike whose window sums pretend the
    current attempt was ordinary would not look like anything the API can
    produce.
    """
    def window_sum(count: int) -> int:
        return prior_unit * (count - 1) + amount_minor

    return VelocitySnapshot(
        count_1m=count_1m,
        count_10m=count_10m,
        count_1h=count_1h,
        amount_sum_1m=window_sum(count_1m),
        amount_sum_10m=window_sum(count_10m),
        amount_sum_1h=window_sum(count_1h),
        countries_1m=min(countries, count_1m),
        countries_10m=countries,
        countries_1h=countries,
        merchants_1m=min(merchants, count_1m),
        merchants_10m=merchants,
        merchants_1h=merchants,
        is_new_merchant=False,
    )


def _sample(
    amount_minor: int,
    snapshot: VelocitySnapshot,
    country: str,
    merchant_id: str | None,
    hour: int,
    label: str,
) -> Sample:
    features = build_features(
        FeatureInputs(
            amount_minor=amount_minor,
            snapshot=snapshot,
            home_country=HOME_COUNTRY,
            country=country,
            merchant_id=merchant_id,
            hour_of_day=hour,
        )
    )
    return Sample(features=features, label=label)


def _normal(rng: random.Random) -> Sample:
    # Ordinary customer: one payment of a mundane size against a quiet window.
    # lognormal(3.4, 1.0) in dollars: median ~$30, occasional ~$500.
    amount_minor = int(round(rng.lognormvariate(3.4, 1.0) * 100))
    amount_minor = max(100, min(amount_minor, 150_000))

    count_1m = rng.choices([1, 2, 3], weights=[88, 9, 3])[0]
    count_10m = count_1m + rng.randint(0, 4)
    count_1h = count_10m + rng.randint(1, 8)
    countries = 1 if rng.random() < 0.95 else 2
    merchants = rng.randint(0, 4)
    # A handful of normal payments are abroad; travel is legal.
    country = HOME_COUNTRY if rng.random() < 0.95 else "CA"
    merchant_id = f"m{rng.randint(1, 40)}" if rng.random() < 0.8 else None
    hour = rng.randint(7, 22) if rng.random() < 0.92 else rng.randint(0, 6)

    return _sample(
        amount_minor,
        _snapshot(
            amount_minor=amount_minor,
            prior_unit=amount_minor,
            count_1m=count_1m,
            count_10m=count_10m,
            count_1h=count_1h,
            countries=countries,
            merchants=merchants,
        ),
        country,
        merchant_id,
        hour,
        "normal",
    )


def _card_testing(rng: random.Random) -> Sample:
    # Stolen-card probing: a burst of tiny charges in a single minute.
    amount_minor = rng.randint(100, 500)
    count_1m = rng.randint(8, 30)
    count_10m = max(count_1m, rng.randint(count_1m, count_1m + 6))
    count_1h = count_10m + rng.randint(0, 10)
    return _sample(
        amount_minor,
        _snapshot(
            amount_minor=amount_minor,
            prior_unit=amount_minor,
            count_1m=count_1m,
            count_10m=count_10m,
            count_1h=count_1h,
            merchants=rng.randint(4, 15),
        ),
        HOME_COUNTRY,
        f"m{rng.randint(1, 200)}",
        rng.randint(0, 23),
        "card_testing",
    )


def _geo_hopping(rng: random.Random) -> Sample:
    # One credential spent from several countries inside a minute.
    amount_minor = rng.randint(2_000, 40_000)
    countries = rng.randint(3, 6)
    count_1m = rng.randint(countries, countries + 4)
    count_10m = count_1m + rng.randint(0, 8)
    count_1h = count_10m + rng.randint(0, 12)
    abroad = rng.choice(["BR", "FR", "NG", "RU", "VN"])
    return _sample(
        amount_minor,
        _snapshot(
            amount_minor=amount_minor,
            prior_unit=amount_minor,
            count_1m=count_1m,
            count_10m=count_10m,
            count_1h=count_1h,
            countries=countries,
            merchants=rng.randint(2, 8),
        ),
        abroad,
        f"m{rng.randint(1, 200)}",
        rng.randint(0, 23),
        "geo_hopping",
    )


def _amount_spike(rng: random.Random) -> Sample:
    # A compromised account cashing out after watching small traffic.
    prior_unit = rng.randint(500, 3_000)
    count_1m = rng.randint(1, 3)
    count_10m = count_1m + rng.randint(2, 6)
    count_1h = count_10m + rng.randint(2, 8)
    amount_minor = prior_unit * rng.randint(10, 60)
    return _sample(
        amount_minor,
        _snapshot(
            amount_minor=amount_minor,
            prior_unit=prior_unit,
            count_1m=count_1m,
            count_10m=count_10m,
            count_1h=count_1h,
        ),
        HOME_COUNTRY,
        f"m{rng.randint(1, 200)}",
        rng.randint(0, 23),
        "amount_spike",
    )


GENERATORS = {
    "normal": _normal,
    "card_testing": _card_testing,
    "geo_hopping": _geo_hopping,
    "amount_spike": _amount_spike,
}


def generate(label: str, count: int, seed: int) -> list[Sample]:
    """Generate ``count`` samples of one behaviour family."""
    generator = GENERATORS[label]
    rng = random.Random(f"{seed}:{label}")  # noqa: S311 - synthetic data, not secrets
    return [generator(rng) for _ in range(count)]


def matrix(samples: list[Sample]) -> np.ndarray:
    """Feature matrix in contract order."""
    return np.asarray(
        [[sample.features[name] for name in FEATURE_NAMES] for sample in samples],
        dtype=float,
    )


__all__ = ["GENERATORS", "Sample", "generate", "matrix"]
