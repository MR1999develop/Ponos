# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06: the 20-minute threshold test (FRI5) next to the 30-minute one.

Hand-computed values: stretches at a constant HR and speed. The threshold HR is the mean of the
whole 20 minutes times 0.95; the threshold pace is the mean pace plus 5% of the time per
kilometre (not of the speed).
"""

from __future__ import annotations

import math
from datetime import date
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.assessment.domain import (
    DEFAULT_PROTOCOL,
    SegmentSelection,
    ThresholdProtocol,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
    evaluate_threshold_test,
)
from ponos_coach.assessment.domain.defaults import METHOD_VERSION
from tests.assessment.builders import LIMITS, OUT_OF_RANGE_BPM, Leg, standard_test, synthetic_run

DAY = date(2026, 3, 2)
TWENTY = ThresholdProtocol.TWENTY_MINUTES
THIRTY = ThresholdProtocol.THIRTY_MINUTES


def twenty_minute_legs(hr: int = 170, speed_ms: float = 3.5) -> list[Leg]:
    """Warm-up, the 20 minutes of the test, cool-down."""
    return [Leg(600, 130, 3.0), Leg(1200, hr, speed_ms), Leg(300, 120, 2.5)]


def evaluate(
    legs: list[Leg], protocol: ThresholdProtocol = TWENTY, **kwargs: object
) -> ThresholdTestResult | ThresholdRejection:
    run = synthetic_run(legs, **kwargs)  # type: ignore[arg-type]
    return evaluate_threshold_test(run, LIMITS, DAY, protocol)


def accepted(outcome: ThresholdTestResult | ThresholdRejection) -> ThresholdTestResult:
    assert isinstance(outcome, ThresholdTestResult), outcome
    return outcome


def test_the_default_protocol_is_the_30_minute_one_and_the_result_says_which_was_used() -> None:
    assert DEFAULT_PROTOCOL is THIRTY
    result = accepted(evaluate_threshold_test(synthetic_run(standard_test()), LIMITS, DAY))

    assert result.protocol is THIRTY
    assert result.method_version == METHOD_VERSION == 2


def test_the_20_minute_segment_is_the_20_minutes_with_the_greatest_distance() -> None:
    result = accepted(evaluate(twenty_minute_legs()))

    assert result.protocol is TWENTY
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0


def test_the_threshold_hr_is_the_mean_of_the_20_minutes_times_095_rounded() -> None:
    # 170 x 0.95 = 161.5: exactly half way, rounds up
    assert accepted(evaluate(twenty_minute_legs(hr=170))).threshold_bpm == 162
    assert accepted(evaluate(twenty_minute_legs(hr=160))).threshold_bpm == 152  # 152.0
    assert accepted(evaluate(twenty_minute_legs(hr=171))).threshold_bpm == 162  # 162.45


def test_the_hr_of_the_whole_20_minutes_counts_not_only_the_last_ones() -> None:
    # 600 s at 150 and 600 s at 170: mean 160 -> 152 (the 30-minute test would take the last 20)
    legs = [Leg(300, 120, 3.0), Leg(600, 150, 3.5), Leg(600, 170, 3.5), Leg(300, 120, 2.5)]

    assert accepted(evaluate(legs)).threshold_bpm == 152


def test_the_pace_is_the_mean_pace_plus_5_percent_of_the_time_per_km() -> None:
    # 3.5 m/s: 285.714 s/km; plus 5% of the time: 300.0 (5% on the speed would give 301)
    assert accepted(evaluate(twenty_minute_legs(speed_ms=3.5))).pace_s_per_km == 300
    # 4 m/s: 250 s/km -> 262.5, a half rounds up
    assert accepted(evaluate(twenty_minute_legs(speed_ms=4.0))).pace_s_per_km == 263


def test_the_pace_zones_follow_the_threshold_pace() -> None:
    result = accepted(evaluate(twenty_minute_legs(speed_ms=3.5)))

    assert result.pace_zone_starts_s_per_km == pytest.approx(
        (300 / 0.78, 300 / 0.88, 300 / 0.94, 300.0)
    )
    assert result.hr_zone_starts_bpm == pytest.approx(
        (162 * 0.72, 162 * 0.85, 162 * 0.95, 162 * 1.03)
    )


def test_without_distance_the_segment_is_chosen_by_hr_and_there_is_no_pace() -> None:
    legs = [Leg(600, 130, 3.0), Leg(1200, 170, 3.5), Leg(300, 120, 2.5)]

    result = accepted(evaluate(legs, with_distance=False))

    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0
    assert (result.threshold_bpm, result.pace_s_per_km) == (162, None)


def test_a_run_too_short_for_30_minutes_can_still_be_a_20_minute_test() -> None:
    legs = [Leg(200, 130, 3.0), Leg(1200, 165, 3.5)]  # 1400 s in all

    assert evaluate(legs, THIRTY) is ThresholdRejection.TOO_SHORT
    assert accepted(evaluate(legs, TWENTY)).threshold_bpm == 157  # 156.75


def test_the_elapsed_time_must_reach_20_minutes() -> None:
    assert evaluate([Leg(1199, 165, 3.5)]) is ThresholdRejection.TOO_SHORT
    assert isinstance(evaluate([Leg(1200, 165, 3.5)]), ThresholdTestResult)


def test_other_sports_are_still_rejected() -> None:
    from ponos_coach.activities.domain import Sport  # noqa: PLC0415 - only this test needs it

    assert evaluate(twenty_minute_legs(), sport=Sport.CYCLING) is ThresholdRejection.WRONG_SPORT


def test_without_a_heart_rate_series_there_is_no_test() -> None:
    outcome = evaluate(twenty_minute_legs(), with_heart_rate=False)

    assert outcome is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_the_coverage_is_90_percent_of_the_20_minutes() -> None:
    ok = [Leg(600, 130, 3.0), Leg(1080, 170, 3.5), Leg(120, OUT_OF_RANGE_BPM, 3.5), Leg(60, 120, 2)]
    bad = [
        Leg(600, 130, 3.0),
        Leg(1078, 170, 3.5),
        Leg(122, OUT_OF_RANGE_BPM, 3.5),
        Leg(60, 120, 2),
    ]

    assert isinstance(evaluate(ok), ThresholdTestResult)  # 1080 of 1200 s: exactly 90%
    assert evaluate(bad) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_pauses_over_60_seconds_in_the_20_minutes_reject_the_test() -> None:
    assert evaluate(twenty_minute_legs(), pauses=[(900, 61)]) is (
        ThresholdRejection.TOO_MANY_PAUSES
    )
    assert isinstance(evaluate(twenty_minute_legs(), pauses=[(900, 60)]), ThresholdTestResult)


def test_a_threshold_at_or_under_the_minimum_hr_is_out_of_range() -> None:
    # mean 52 x 0.95 = 49.4 -> 49, not above the minimum of 50
    legs = [Leg(300, 55, 3.0), Leg(1200, 52, 3.5)]

    assert evaluate(legs) is ThresholdRejection.THRESHOLD_OUT_OF_RANGE


def test_the_history_entry_keeps_the_protocol() -> None:
    entry = ThresholdTest.of(accepted(evaluate(twenty_minute_legs())))

    assert entry.protocol is TWENTY
    assert entry.method_version == METHOD_VERSION


def test_the_same_run_gives_the_same_outcome_every_time() -> None:
    run = synthetic_run(twenty_minute_legs())

    assert evaluate_threshold_test(run, LIMITS, DAY, TWENTY) == evaluate_threshold_test(
        run, LIMITS, DAY, TWENTY
    )


def _half_up(value: Fraction) -> int:
    return math.floor(value + Fraction(1, 2))


@given(
    hr=st.integers(min_value=100, max_value=190), speed_dm_s=st.integers(min_value=20, max_value=60)
)
def test_a_constant_effort_gives_the_naive_exact_threshold(hr: int, speed_dm_s: int) -> None:
    """Against a naive computation with fractions: HR x 95%, time per km x 105%."""
    speed = speed_dm_s / 10
    result = accepted(evaluate([Leg(1200, hr, speed)]))

    expected_hr = _half_up(Fraction(hr) * 95 / 100)
    pace = Fraction(1000) / Fraction(speed_dm_s, 10) * 105 / 100
    assert result.threshold_bpm == expected_hr
    assert result.pace_s_per_km == _half_up(pace)
