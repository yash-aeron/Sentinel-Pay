"""The feature contract: order, values, and the derived ratio feature."""

from __future__ import annotations

import math

from app.services.risk.features import (
    FEATURE_NAMES,
    FeatureInputs,
    build_features,
    to_vector,
)
from app.services.velocity import VelocitySnapshot


def inputs(**overrides: object) -> FeatureInputs:
    base: dict[str, object] = {
        "amount_minor": 2_500,
        "snapshot": VelocitySnapshot(
            count_1m=2,
            count_10m=3,
            count_1h=7,
            amount_sum_1m=5_000,
            amount_sum_10m=7_500,
            amount_sum_1h=17_500,
            countries_1m=1,
            countries_10m=2,
            countries_1h=3,
            merchants_1m=1,
            merchants_10m=2,
            merchants_1h=4,
            is_new_merchant=True,
        ),
        "home_country": "US",
        "country": "US",
        "merchant_id": "m-42",
        "hour_of_day": 14,
    }
    return FeatureInputs(**(base | overrides))  # type: ignore[arg-type]


def test_keys_come_out_in_contract_order():
    features = build_features(inputs())
    assert tuple(features) == FEATURE_NAMES


def test_every_value_is_a_finite_float():
    features = build_features(inputs())
    assert all(isinstance(value, float) and math.isfinite(value) for value in features.values())


def test_values_map_their_sources():
    features = build_features(inputs())
    assert features["log_amount"] == math.log1p(2_500 / 100)
    assert features["count_1m"] == 2.0
    assert features["count_10m"] == 3.0
    assert features["count_1h"] == 7.0
    assert features["log_amount_sum_1m"] == math.log1p(50.0)
    assert features["countries_1h"] == 3.0
    assert features["merchants_1h"] == 4.0
    assert features["hour_of_day"] == 14.0


def test_cross_border_flag_follows_home_comparison():
    assert build_features(inputs(country="FR"))["cross_border"] == 1.0
    assert build_features(inputs())["cross_border"] == 0.0


def test_new_merchant_requires_a_merchant_on_the_request():
    # The snapshot says new, and the request carries a merchant.
    assert build_features(inputs())["new_merchant"] == 1.0
    # No merchant on the request: is_new_merchant is meaningless, not 1.
    assert build_features(inputs(merchant_id=None))["new_merchant"] == 0.0


def test_ratio_feature_is_log_of_amount_over_prior_window_average():
    # Snapshot includes the current attempt: 3 prior attempts of 100 each,
    # current attempt 2_500 -> ratio 25 -> log(25).
    features = build_features(
        inputs(
            amount_minor=2_500,
            snapshot=VelocitySnapshot(
                count_1m=1,
                count_10m=4,
                count_1h=4,
                amount_sum_1m=2_500,
                amount_sum_10m=2_500 + 3 * 100,
                amount_sum_1h=2_800,
                countries_1m=1,
                countries_10m=1,
                countries_1h=1,
                merchants_1m=0,
                merchants_10m=0,
                merchants_1h=0,
                is_new_merchant=False,
            ),
        )
    )
    assert features["log_amount_vs_prior_avg_10m"] == math.log(25.0)


def test_ratio_feature_is_zero_without_prior_history():
    # Only this attempt in the window.
    only_current = VelocitySnapshot(
        count_1m=1,
        count_10m=1,
        count_1h=1,
        amount_sum_1m=2_500,
        amount_sum_10m=2_500,
        amount_sum_1h=2_500,
        countries_1m=1,
        countries_10m=1,
        countries_1h=1,
        merchants_1m=0,
        merchants_10m=0,
        merchants_1h=0,
        is_new_merchant=False,
    )
    assert build_features(inputs(snapshot=only_current))["log_amount_vs_prior_avg_10m"] == 0.0


def test_to_vector_round_trips_the_contract_order():
    features = build_features(inputs())
    assert to_vector(features) == [features[name] for name in FEATURE_NAMES]
