# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Load domain types: valid by construction."""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    DEFAULT_LT1_PERCENT,
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    default_lt1,
    lt1_for_sport,
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


def test_threshold_for_sport_is_the_running_one_for_every_sport_without_an_other_lt2() -> None:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)

    assert [threshold_for_sport(physiology, s) for s in Sport] == [170] * len(Sport)


# --- LT1: default share of LT2, rounded half up, brought to the limits ---------------------------


def test_the_default_lt1_share_is_83_percent() -> None:
    assert DEFAULT_LT1_PERCENT == 83


@pytest.mark.parametrize(
    ("lt2", "expected"),
    [
        (170, 141),  # 141.1: rounds down
        (171, 142),  # 141.93: rounds up
        (150, 125),  # 124.5 exactly: half rounds up (Python's round() would give 124)
        (100, 83),
        (149, 124),  # 123.67
        (190, 158),  # 157.7
    ],
)
def test_the_default_lt1_rounds_half_up(lt2: int, expected: int) -> None:
    assert default_lt1(lt2, min_bpm=50) == expected


@pytest.mark.parametrize(
    ("lt2", "expected"),
    [
        (61, 51),
        (60, 51),
        (62, 51),
        (63, 52),
        (51, 51),
    ],  # 83% of 61 = 50.6 -> 51 (on the minimum + 1)
)
def test_the_default_lt1_is_brought_just_above_the_resting_rate(lt2: int, expected: int) -> None:
    # 83% of 60 is 49.8: below the minimum + 1 it is clamped; 62 gives 51.46 and 63 gives 52.29.
    assert default_lt1(lt2, min_bpm=50) == expected


def test_the_default_lt1_never_exceeds_lt2() -> None:
    assert default_lt1(51, min_bpm=50) == 51  # only one bpm above the minimum: LT1 equals LT2


def physiology_with(
    lt2: int = 170,
    *,
    run_lt1: int | None = None,
    other: int | None = None,
    other_lt1: int | None = None,
) -> Physiology:
    def opt(bpm: int | None) -> ThresholdHeartRate | None:
        return None if bpm is None else ThresholdHeartRate(bpm)

    return Physiology(
        HeartRateLimits(50, 190),
        ThresholdHeartRate(lt2),
        Sex.MALE,
        other_threshold=opt(other),
        run_lt1=opt(run_lt1),
        other_lt1=opt(other_lt1),
    )


def test_lt1_for_sport_is_the_entered_value_of_the_group_else_the_default() -> None:
    p = physiology_with(170, run_lt1=140, other=150)

    assert lt1_for_sport(p, Sport.RUNNING) == 140
    assert lt1_for_sport(p, Sport.TRAIL) == 140
    assert lt1_for_sport(p, Sport.CYCLING) == default_lt1(150, 50)  # 83% of the other LT2: 125
    assert lt1_for_sport(p, Sport.WALKING) == 125


def test_the_default_lt1_of_other_sports_follows_the_running_lt2_without_their_own() -> None:
    assert lt1_for_sport(physiology_with(170), Sport.OTHER) == 141


@pytest.mark.parametrize(
    ("lt1", "accepted"), [(49, False), (50, False), (51, True), (169, True), (170, False)]
)
def test_run_lt1_must_lie_between_the_resting_rate_and_lt2_both_excluded(
    lt1: int, accepted: bool
) -> None:
    if accepted:
        assert physiology_with(170, run_lt1=lt1).run_lt1 == ThresholdHeartRate(lt1)
    else:
        with pytest.raises(ValueError, match="LT1"):
            physiology_with(170, run_lt1=lt1)


@pytest.mark.parametrize(("lt1", "accepted"), [(149, True), (150, False), (151, False)])
def test_other_lt1_is_checked_against_the_other_lt2(lt1: int, accepted: bool) -> None:
    if accepted:
        assert physiology_with(170, other=150, other_lt1=lt1).other_lt1 is not None
    else:
        with pytest.raises(ValueError, match="LT1"):
            physiology_with(170, other=150, other_lt1=lt1)


def test_other_lt1_is_checked_against_the_running_lt2_when_the_other_lt2_is_empty() -> None:
    assert physiology_with(170, other_lt1=169).other_lt1 is not None
    with pytest.raises(ValueError, match="LT1"):
        physiology_with(170, other_lt1=170)


def test_typical_intensity_is_defined_and_below_threshold_for_every_sport() -> None:
    assert all(0.0 < typical_intensity(s) <= 1.0 for s in Sport)
