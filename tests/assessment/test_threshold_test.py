# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06: the 30-minute threshold test on synthetic tracks.

Hand-computed values: the tracks are stretches at a constant HR and speed, so the segment, the
mean HR and the pace of the last 20 minutes are known in advance.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import numpy as np
import pytest

from ponos_coach.activities.domain import Activity, DistanceSeries, HeartRateSeries, Sport
from ponos_coach.assessment.domain import (
    SegmentSelection,
    ThresholdRejection,
    ThresholdTestResult,
    evaluate_threshold_test,
)
from ponos_coach.load.domain import HeartRateLimits
from tests.assessment.builders import LIMITS, OUT_OF_RANGE_BPM, Leg, standard_test, synthetic_run

DAY = date(2026, 3, 2)


def evaluate(
    legs: list[Leg], limits: HeartRateLimits = LIMITS, **kwargs: object
) -> ThresholdTestResult | ThresholdRejection:
    return evaluate_threshold_test(synthetic_run(legs, **kwargs), limits, DAY)  # type: ignore[arg-type]


def accepted(outcome: ThresholdTestResult | ThresholdRejection) -> ThresholdTestResult:
    assert isinstance(outcome, ThresholdTestResult), outcome
    return outcome


# --- The segment and the threshold ---------------------------------------------------------


def test_the_segment_is_the_30_minutes_with_the_greatest_distance_after_the_warm_up() -> None:
    result = accepted(evaluate(standard_test(warm_up_s=600)))

    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0
    assert result.segment_start == datetime(2026, 3, 2, 7, 10, tzinfo=UTC)
    assert result.test_day == DAY


def test_the_threshold_hr_is_the_mean_of_the_last_20_minutes_not_of_all_the_30() -> None:
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(1200, 170, 3.5), Leg(300, 120, 2.5)]

    result = accepted(evaluate(legs))

    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 170  # the first 10 minutes at 150 do not count


def test_the_threshold_hr_is_a_time_weighted_mean_rounded_to_a_whole_bpm() -> None:
    # Last 20 minutes: 400 s at 160 and 800 s at 170: 166.67 -> 167
    legs = [Leg(600, 130, 3.0), Leg(600, 150, 3.5), Leg(400, 160, 3.5), Leg(800, 170, 3.5)]

    result = accepted(evaluate([*legs, Leg(300, 120, 2.5)]))

    assert result.threshold_bpm == 167


def test_a_mean_exactly_half_way_rounds_up() -> None:
    # 600 s at 164 and 600 s at 165 in the last 20 minutes: 164.5 -> 165
    legs = [Leg(600, 150, 3.5), Leg(600, 164, 3.5), Leg(600, 165, 3.5)]

    assert accepted(evaluate([Leg(300, 120, 2.0), *legs])).threshold_bpm == 165


def test_the_pace_is_the_time_over_the_distance_of_the_last_20_minutes() -> None:
    # 3.5 m/s: 4200 m in 1200 s -> 285.71 s/km -> 286
    result = accepted(evaluate(standard_test(speed_ms=3.5)))

    assert result.pace_s_per_km == 286


def test_the_pace_zones_start_where_the_speed_reaches_each_share_of_the_threshold_speed() -> None:
    # 4 m/s: 250 s/km; Z2 from 78% of the speed = 250 / 0.78 s/km, and so on
    result = accepted(evaluate(standard_test(speed_ms=4.0)))

    assert result.pace_s_per_km == 250
    assert result.pace_zone_starts_s_per_km == pytest.approx(
        (250 / 0.78, 250 / 0.88, 250 / 0.94, 250.0)
    )


def test_the_hr_zones_follow_the_threshold_just_found() -> None:
    result = accepted(evaluate(standard_test(hr=165)))

    assert result.hr_zone_starts_bpm == pytest.approx(
        (165 * 0.75, 165 * 0.85, 165 * 0.95, 165 * 1.03)
    )


def test_the_result_counts_the_hr_samples_it_used() -> None:
    result = accepted(evaluate(standard_test()))

    assert result.valid_hr_samples == 1200  # one record a second for the last 20 minutes


def test_a_trail_run_is_a_valid_test_too() -> None:
    assert isinstance(evaluate(standard_test(), sport=Sport.TRAIL), ThresholdTestResult)


def test_the_first_window_wins_a_tie_in_distance() -> None:
    legs = [Leg(3600, 160, 3.0)]  # the same distance from every start

    result = accepted(evaluate(legs))

    assert result.segment_start_s == 0.0


def test_the_result_does_not_depend_on_anything_but_the_activity_and_the_limits() -> None:
    run = synthetic_run(standard_test())

    assert evaluate_threshold_test(run, LIMITS, DAY) == evaluate_threshold_test(run, LIMITS, DAY)


# --- Without distance: the highest mean valid HR ----------------------------------------------


def test_without_distance_the_segment_is_the_30_minutes_with_the_highest_valid_hr() -> None:
    result = accepted(evaluate(standard_test(), with_distance=False))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 165
    assert result.pace_s_per_km is None
    assert result.pace_zone_starts_s_per_km is None


def test_a_distance_that_never_grows_counts_as_no_distance() -> None:
    result = accepted(evaluate([Leg(600, 130, 0.0), Leg(1800, 165, 0.0), Leg(300, 120, 0.0)]))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0
    assert result.pace_s_per_km is None


def test_a_hr_window_tie_goes_to_the_first() -> None:
    result = accepted(evaluate([Leg(3600, 150)], with_distance=False))

    assert result.segment_start_s == 0.0


def test_without_distance_samples_outside_the_limits_do_not_raise_the_window() -> None:
    # A burst of 255 bpm (a strap glitch) after the effort must not look like a harder stretch.
    legs = [Leg(600, 130), Leg(1800, 165), Leg(300, 255)]

    result = accepted(evaluate(legs, with_distance=False))

    assert result.threshold_bpm == 165


def test_a_hr_window_with_few_valid_seconds_does_not_win_for_its_high_mean() -> None:
    # Window 0 is complete at 150 bpm; a later one ends on 100 s at 180 bpm after 1100 s of
    # invalid HR: a higher mean, but it would be rejected for its coverage.
    legs = [Leg(1700, 150), Leg(1100, OUT_OF_RANGE_BPM), Leg(100, 180)]

    result = accepted(evaluate(legs, with_distance=False))

    assert result.segment_start_s == 0.0
    assert result.threshold_bpm == 150


def test_a_hr_window_whose_coverage_is_exactly_90_percent_can_win() -> None:
    # The best window reaches exactly 1080 valid seconds in its last 20 minutes: not a candidate
    # at a breakpoint, but at the point where the limit is reached.
    # The mean rises with the start until the 180 bpm part is all in; the valid seconds of the
    # last 20 minutes fall with it and reach 1080 at second 120, which is no breakpoint.
    legs = [Leg(1000, 150), Leg(800, 180), Leg(1500, OUT_OF_RANGE_BPM)]

    result = accepted(evaluate(legs, with_distance=False))

    assert result.segment_start_s == pytest.approx(120.0)
    assert result.threshold_bpm == 172


def test_when_no_hr_window_has_enough_valid_seconds_the_test_is_rejected() -> None:
    legs = [Leg(900, 150), Leg(1800, OUT_OF_RANGE_BPM), Leg(900, 150)]

    assert evaluate(legs, with_distance=False) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def _with_distance_jump(at_s: int, jump_m: float = 20_000.0) -> Activity:
    activity = synthetic_run(standard_test())
    assert activity.distance is not None
    seconds, meters = activity.distance.seconds, activity.distance.meters
    jumped = DistanceSeries(seconds, meters + np.where(seconds >= at_s, jump_m, 0.0))
    return replace(activity, distance=jumped)


@pytest.mark.parametrize("at_s", [300, 2500])
def test_a_distance_jump_outside_the_test_does_not_move_the_segment(at_s: int) -> None:
    result = accepted(evaluate_threshold_test(_with_distance_jump(at_s), LIMITS, DAY))

    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0
    assert result.pace_s_per_km is not None


def test_a_distance_jump_inside_the_test_falls_back_to_the_hr_and_gives_no_pace() -> None:
    result = accepted(evaluate_threshold_test(_with_distance_jump(1500), LIMITS, DAY))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 165
    assert result.pace_s_per_km is None


def test_a_best_window_with_a_walking_pace_is_not_trusted_for_the_distance() -> None:
    legs = [Leg(600, 130, 0.2), Leg(1800, 165, 0.2), Leg(300, 120, 0.2)]  # 5000 s/km

    result = accepted(evaluate(legs))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.pace_s_per_km is None


# --- Rejections ---------------------------------------------------------------------------


@pytest.mark.parametrize("sport", [Sport.CYCLING, Sport.WALKING, Sport.HIKING, Sport.OTHER])
def test_only_running_sports_can_be_the_test(sport: Sport) -> None:
    assert evaluate(standard_test(), sport=sport) is ThresholdRejection.WRONG_SPORT


def test_an_interrupted_test_is_rejected_as_too_short() -> None:
    assert evaluate([Leg(600, 130), Leg(900, 165, 3.5)]) is ThresholdRejection.TOO_SHORT


def test_one_second_under_30_minutes_of_elapsed_time_is_too_short() -> None:
    run = synthetic_run([Leg(1799, 165, 3.5)])

    assert evaluate_threshold_test(run, LIMITS, DAY) is ThresholdRejection.TOO_SHORT


def test_exactly_30_minutes_of_elapsed_time_is_enough() -> None:
    # Records at seconds 0 to 1800: the 30 minutes are covered
    assert isinstance(evaluate([Leg(1800, 165, 3.5)]), ThresholdTestResult)


def test_30_minutes_of_elapsed_time_but_records_for_less_is_too_short() -> None:
    run = synthetic_run([Leg(1500, 165, 3.5)], elapsed_s=1800.0)

    assert evaluate_threshold_test(run, LIMITS, DAY) is ThresholdRejection.TOO_SHORT


def test_an_activity_without_hr_cannot_be_the_test() -> None:
    outcome = evaluate(standard_test(), with_heart_rate=False)

    assert outcome is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def _with_invalid_seconds_in_the_last_20_minutes(invalid_s: int) -> list[Leg]:
    """A standard test whose last 20 minutes have `invalid_s` seconds of out-of-range HR."""
    return [
        Leg(600, 130, 3.0),
        Leg(600, 165, 3.5),
        Leg(invalid_s, OUT_OF_RANGE_BPM, 3.5),
        Leg(1200 - invalid_s, 165, 3.5),
        Leg(300, 120, 2.5),
    ]


def test_valid_hr_for_exactly_90_percent_of_the_last_20_minutes_is_enough() -> None:
    result = accepted(evaluate(_with_invalid_seconds_in_the_last_20_minutes(120)))

    assert result.threshold_bpm == 165  # the invalid seconds do not enter the mean


def test_valid_hr_below_90_percent_of_the_last_20_minutes_is_rejected() -> None:
    outcome = evaluate(_with_invalid_seconds_in_the_last_20_minutes(121))

    assert outcome is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_missing_hr_counts_like_invalid_hr() -> None:
    legs = [Leg(600, 130), Leg(600, 165, 3.5), Leg(300, None, 3.5), Leg(900, 165, 3.5)]

    assert evaluate(legs) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_an_hr_that_is_all_out_of_the_limits_is_rejected() -> None:
    legs = standard_test(hr=OUT_OF_RANGE_BPM)

    assert evaluate(legs) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


@pytest.mark.parametrize("hr", [190, 50])
def test_a_threshold_equal_to_max_or_min_hr_is_rejected(hr: int) -> None:
    # The profile range is open: a mean AT a limit says the limit is wrong, not the threshold
    assert hr in (LIMITS.min_bpm, LIMITS.max_bpm)
    assert evaluate(standard_test(hr=hr)) is ThresholdRejection.THRESHOLD_OUT_OF_RANGE


@pytest.mark.parametrize("hr", [51, 189])
def test_a_threshold_one_bpm_inside_the_limits_is_accepted(hr: int) -> None:
    assert accepted(evaluate(standard_test(hr=hr))).threshold_bpm == hr


def test_a_threshold_under_40_bpm_is_rejected_even_with_a_low_min_hr() -> None:
    low_limits = HeartRateLimits(20, 190)

    assert evaluate(standard_test(hr=35), low_limits) is ThresholdRejection.THRESHOLD_OUT_OF_RANGE


def test_the_limits_are_those_given_not_a_fixed_range() -> None:
    narrow = HeartRateLimits(50, 160)  # a mean of 165 is above this profile's maximum

    assert evaluate(standard_test(hr=165), narrow) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


# --- Pauses ---------------------------------------------------------------------------------


def test_a_pause_of_exactly_60_seconds_in_the_segment_is_accepted() -> None:
    assert isinstance(
        evaluate(standard_test(), pauses=[(1200, 60)]),  # records 60 s apart: a 60 s gap
        ThresholdTestResult,
    )


def test_a_pause_of_61_seconds_in_the_segment_is_rejected() -> None:
    assert evaluate(standard_test(), pauses=[(1200, 61)]) is ThresholdRejection.TOO_MANY_PAUSES


def test_pauses_add_up() -> None:
    outcome = evaluate(standard_test(), pauses=[(1000, 30), (1500, 31)])

    assert outcome is ThresholdRejection.TOO_MANY_PAUSES


def test_gaps_up_to_10_seconds_are_not_pauses() -> None:
    pauses = [(700 + 100 * k, 10) for k in range(15)]  # fifteen gaps of exactly 10 s

    assert isinstance(evaluate(standard_test(), pauses=pauses), ThresholdTestResult)


def test_a_pause_in_the_warm_up_does_not_count() -> None:
    assert isinstance(evaluate(standard_test(), pauses=[(300, 120)]), ThresholdTestResult)


def test_a_pause_in_the_cool_down_does_not_count() -> None:
    assert isinstance(evaluate(standard_test(), pauses=[(2600, 120)]), ThresholdTestResult)


# --- Details of the data --------------------------------------------------------------------


def test_samples_every_five_seconds_work_like_samples_every_second() -> None:
    # A watch in smart recording: HR every 5 s. Build it from a 1 Hz run by keeping every 5th.
    run = synthetic_run(standard_test())
    assert run.heart_rate is not None
    assert run.distance is not None
    sparse = replace(
        run,
        heart_rate=HeartRateSeries(run.heart_rate.seconds[::5], run.heart_rate.bpm[::5]),
        distance=DistanceSeries(run.distance.seconds[::5], run.distance.meters[::5]),
    )

    result = accepted(evaluate_threshold_test(sparse, LIMITS, DAY))

    assert result.threshold_bpm == 165
    assert result.segment_start_s == 600.0
    assert result.valid_hr_samples == 240
