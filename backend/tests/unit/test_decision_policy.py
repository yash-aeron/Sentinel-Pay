"""Decision bands and rule floors."""

from __future__ import annotations

import pytest

from app.services.risk.decision import BLOCK_FLOOR, RiskPolicy


def test_default_bands_map_the_unit_interval():
    policy = RiskPolicy()
    assert policy.band(0.0) == "APPROVE"
    assert policy.band(0.44) == "APPROVE"
    assert policy.band(0.45) == "REVIEW"  # approve_max itself is already review
    assert policy.band(0.749) == "REVIEW"
    assert policy.band(0.75) == "DECLINE"  # review_max itself declines
    assert policy.band(1.0) == "DECLINE"


def test_thresholds_must_be_ordered():
    with pytest.raises(ValueError, match="must satisfy"):
        RiskPolicy(approve_max=0.8, review_max=0.5)
    with pytest.raises(ValueError, match="must satisfy"):
        RiskPolicy(approve_max=0.5, review_max=0.5)
    with pytest.raises(ValueError, match="must satisfy"):
        RiskPolicy(approve_max=-0.1, review_max=0.5)
    with pytest.raises(ValueError, match="must satisfy"):
        RiskPolicy(approve_max=0.4, review_max=1.5)


def test_review_floor_sits_inside_the_review_band():
    policy = RiskPolicy(approve_max=0.4, review_max=0.8)
    floor = policy.review_floor
    assert floor == pytest.approx(0.6)
    assert policy.band(floor) == "REVIEW"
    # No threshold tweak can push a rule-lifted score into APPROVE or DECLINE
    # by accident: the floor is the midpoint, so it moves with the bands.
    for approve_max, review_max in [(0.1, 0.2), (0.45, 0.75), (0.3, 0.95)]:
        policy = RiskPolicy(approve_max=approve_max, review_max=review_max)
        assert policy.band(policy.review_floor) == "REVIEW"


def test_block_floor_always_declines():
    assert BLOCK_FLOOR == 1.0
    assert RiskPolicy().band(BLOCK_FLOOR) == "DECLINE"
