# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Load domain types: valid by construction."""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    threshold_for_sport,
    typical_intensity,
)


@pytest.mark.parametrize(("low", "high"), [(50, 190), (20, 21), (249, 250)])
def test_limits_accept_valid_ranges(low: int, high: int) -> None:
    assert HeartRateLimits(low, high).reserve_bpm == high - low


@pytest.mark.parametrize(("low", "high"), [(19, 190), (50, 50), (90, 80), (50, 251)])
def test_limits_reject_invalid_ranges(low: int, high: int) -> None:
    with pytest.raises(ValueError, match="HR"):
        HeartRateLimits(low, high)


@pytest.mark.parametrize("bpm", [39, 251, 0, -1])
def test_threshold_rejects_out_of_range(bpm: int) -> None:
    with pytest.raises(ValueError, match="threshold"):
        ThresholdHeartRate(bpm)


@pytest.mark.parametrize("threshold", [191, 50, 49])
def test_physiology_rejects_threshold_outside_limits(threshold: int) -> None:
    with pytest.raises(ValueError, match="threshold"):
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold), Sex.MALE)


@pytest.mark.parametrize("threshold", [51, 52, 54, 55, 190])
def test_physiology_accepts_every_threshold_above_minimum_up_to_maximum(threshold: int) -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold), Sex.FEMALE)

    assert physiology.threshold.bpm == threshold


def test_banister_coefficients() -> None:
    assert Sex.MALE.banister_k == 1.92
    assert Sex.FEMALE.banister_k == 1.67


def test_threshold_for_sport_only_cycling_is_lower() -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
    assert threshold_for_sport(physiology, Sport.CYCLING) == 166
    others = [threshold_for_sport(physiology, s) for s in Sport if s is not Sport.CYCLING]
    assert others == [170] * (len(Sport) - 1)


@pytest.mark.parametrize(
    ("threshold", "bike"),
    [(51, 51), (52, 51), (54, 51), (55, 51), (56, 52), (170, 166)],
)
def test_bike_threshold_is_clamped_just_above_the_minimum_instead_of_rejected(
    threshold: int, bike: int
) -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold), Sex.MALE)

    assert threshold_for_sport(physiology, Sport.CYCLING) == bike


def test_typical_intensity_is_defined_and_below_threshold_for_every_sport() -> None:
    assert all(0.0 < typical_intensity(s) <= 1.0 for s in Sport)
