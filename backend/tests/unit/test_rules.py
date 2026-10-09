"""Deterministic rules: each rule fires on its shape and stays quiet otherwise."""

from __future__ import annotations

import pytest

from app.services.risk import rules
from app.services.velocity import VelocitySnapshot


def snapshot(**overrides: int) -> VelocitySnapshot:
    """A quiet snapshot; tests override the fields a rule cares about."""
    base = {
        "count_1m": 1,
        "count_10m": 1,
        "count_1h": 1,
        "amount_sum_1m": 2_000,
        "amount_sum_10m": 2_000,
        "amount_sum_1h": 2_000,
        "countries_1m": 1,
        "countries_10m": 1,
        "countries_1h": 1,
        "merchants_1m": 0,
        "merchants_10m": 0,
        "merchants_1h": 0,
        "is_new_merchant": False,
    }
    return VelocitySnapshot(**base | overrides)


def inputs(
    amount_minor: int = 2_000,
    snap: VelocitySnapshot | None = None,
    **kw: str,
) -> rules.RuleInputs:
    params = {"home_country": "US", "country": "US", "merchant_id": None} | kw
    return rules.RuleInputs(
        amount_minor=amount_minor,
        snapshot=snap if snap is not None else snapshot(),
        **params,  # type: ignore[arg-type]
    )


def codes(result: rules.RuleResult) -> set[str]:
    return set(result.codes)


def test_quiet_traffic_fires_nothing():
    assert rules.evaluate(inputs()).hits == ()


def test_card_testing_fires_block_on_burst_of_small_charges():
    # 6 attempts in a minute averaging 200 minor units.
    result = rules.evaluate(inputs(200, snapshot(count_1m=6, amount_sum_1m=1_200)))
    assert "R_CARD_TESTING" in codes(result)
    assert result.has_block


def test_card_testing_requires_both_burst_and_small_average():
    # Burst with a large average is a busy customer, not a probe.
    assert "R_CARD_TESTING" not in codes(rules.evaluate(
        inputs(400, snapshot(count_1m=6, amount_sum_1m=2_400))
    ))
    # Small charges without the burst are just small charges.
    assert "R_CARD_TESTING" not in codes(rules.evaluate(
        inputs(200, snapshot(count_1m=5, amount_sum_1m=1_000))
    ))


def test_geo_hop_fires_review_at_three_countries_in_a_minute():
    result = rules.evaluate(inputs(snap=snapshot(countries_1m=3)))
    assert codes(result) == {"R_GEO_HOP_3_COUNTRIES_60S"}
    assert result.needs_review and not result.has_block
    assert "R_GEO_HOP_3_COUNTRIES_60S" not in codes(
        rules.evaluate(inputs(snap=snapshot(countries_1m=2)))
    )


def test_velocity_block_at_twenty_attempts_in_ten_minutes():
    result = rules.evaluate(inputs(snap=snapshot(count_10m=20)))
    assert "R_VELOCITY_20_10M" in codes(result)
    assert result.has_block
    assert "R_VELOCITY_20_10M" not in codes(rules.evaluate(inputs(snap=snapshot(count_10m=19))))


def test_velocity_review_at_forty_attempts_in_an_hour():
    result = rules.evaluate(inputs(snap=snapshot(count_1h=40)))
    assert codes(result) == {"R_VELOCITY_40_1H"}
    assert "R_VELOCITY_40_1H" not in codes(rules.evaluate(inputs(snap=snapshot(count_1h=39))))


def test_merchant_spray_fires_review_at_eight_merchants():
    result = rules.evaluate(inputs(snap=snapshot(merchants_10m=8)))
    assert codes(result) == {"R_MERCHANT_SPRAY_8_10M"}
    assert "R_MERCHANT_SPRAY_8_10M" not in codes(
        rules.evaluate(inputs(snap=snapshot(merchants_10m=7)))
    )


def test_amount_spike_fires_against_prior_window_average():
    # Snapshot includes the current attempt: 4 prior attempts averaging 100,
    # current attempt 4_000 = 40x the prior average.
    result = rules.evaluate(
        inputs(4_000, snapshot(count_10m=5, amount_sum_10m=4_000 + 4 * 100))
    )
    assert "R_AMOUNT_SPIKE_8X" in codes(result)
    assert result.needs_review and not result.has_block


def test_amount_spike_needs_prior_history():
    # 8x of two prior attempts is not yet a pattern.
    assert "R_AMOUNT_SPIKE_8X" not in codes(
        rules.evaluate(inputs(4_000, snapshot(count_10m=3, amount_sum_10m=4_000 + 2 * 100)))
    )
    # Large but consistent with history.
    assert "R_AMOUNT_SPIKE_8X" not in codes(
        rules.evaluate(inputs(400, snapshot(count_10m=5, amount_sum_10m=400 + 4 * 100)))
    )


def test_cross_border_large_fires_review_abroad_above_floor():
    result = rules.evaluate(inputs(100_000, country="FR"))
    assert codes(result) == {"R_CROSS_BORDER_LARGE"}
    assert "R_CROSS_BORDER_LARGE" not in codes(rules.evaluate(inputs(99_999, country="FR")))
    assert "R_CROSS_BORDER_LARGE" not in codes(rules.evaluate(inputs(100_000)))


def test_block_dominates_review_when_both_fire():
    result = rules.evaluate(inputs(200, snapshot(count_1m=6, amount_sum_1m=1_200, countries_1m=3)))
    assert result.has_block
    assert {"R_CARD_TESTING", "R_GEO_HOP_3_COUNTRIES_60S"} <= codes(result)


@pytest.mark.parametrize(
    ("snap", "amount", "country", "expected"),
    [
        (snapshot(count_1m=6, amount_sum_1m=1_200), 200, "US", "R_CARD_TESTING"),
        (snapshot(countries_1m=3), 2_000, "US", "R_GEO_HOP_3_COUNTRIES_60S"),
        (snapshot(count_10m=20), 2_000, "US", "R_VELOCITY_20_10M"),
        (snapshot(count_1h=40), 2_000, "US", "R_VELOCITY_40_1H"),
        (snapshot(merchants_10m=8), 2_000, "US", "R_MERCHANT_SPRAY_8_10M"),
        (snapshot(count_10m=5, amount_sum_10m=4_400), 4_000, "US", "R_AMOUNT_SPIKE_8X"),
        (snapshot(), 100_000, "FR", "R_CROSS_BORDER_LARGE"),
    ],
)
def test_every_rule_produces_a_stable_code(
    snap: VelocitySnapshot, amount: int, country: str, expected: str
):
    assert expected in codes(rules.evaluate(inputs(amount, snap, country=country)))
