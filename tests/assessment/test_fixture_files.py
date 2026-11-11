# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06 from the files: FIT fixtures with distance, through the real reader.

The values are hand-computed from `make_fixtures.build_threshold`: 10' of warm-up, a 30' test whose
last 20' are at 168 bpm and 3.6 m/s (4320 m: 278 s/km), 5' of cool-down.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pytest

from ponos_coach.activities.fit_decoder import decode_activity
from ponos_coach.assessment.domain import (
    SegmentSelection,
    ThresholdRejection,
    ThresholdTestResult,
    evaluate_threshold_test,
)
from tests.assessment.builders import LIMITS

DATA = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "fit"
DAY = date(2026, 3, 14)


def evaluate_file(name: str) -> ThresholdTestResult | ThresholdRejection:
    activity = decode_activity((DATA / name).read_bytes())
    return evaluate_threshold_test(activity, LIMITS, DAY)


def test_a_run_with_distance_gives_the_segment_the_threshold_and_the_pace() -> None:
    result = evaluate_file("run_threshold_test.fit")

    assert isinstance(result, ThresholdTestResult)
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 168
    assert result.pace_s_per_km == 278
    assert result.valid_hr_samples == 1200


def test_the_same_run_without_distance_gives_the_same_threshold_and_no_pace() -> None:
    result = evaluate_file("run_threshold_no_distance.fit")

    assert isinstance(result, ThresholdTestResult)
    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 600.0
    assert result.threshold_bpm == 168
    assert result.pace_s_per_km is None


def test_a_run_with_speed_only_integrates_it_into_a_distance() -> None:
    result = evaluate_file("run_threshold_speed_only.fit")

    assert isinstance(result, ThresholdTestResult)
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.threshold_bpm == 168
    assert result.pace_s_per_km is not None
    assert abs(result.pace_s_per_km - 278) <= 2  # the integral smooths the change of speed


def test_a_run_with_a_pause_of_90_seconds_in_the_segment_is_rejected() -> None:
    assert evaluate_file("run_threshold_paused.fit") is ThresholdRejection.TOO_MANY_PAUSES


def test_the_distance_series_of_a_file_has_the_time_axis_of_the_hr_series() -> None:
    activity = decode_activity((DATA / "run_threshold_test.fit").read_bytes())

    assert activity.distance is not None
    assert activity.heart_rate is not None
    assert np.array_equal(activity.distance.seconds, activity.heart_rate.seconds)
    assert activity.distance.meters[0] == 0.0
    assert activity.distance.meters[-1] == pytest.approx(
        600 * 3.0 + 600 * 3.4 + 1200 * 3.6 + 300 * 2.5
    )


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("walk_no_hr.fit", ThresholdRejection.WRONG_SPORT),
        ("bike_easy.fit", ThresholdRejection.WRONG_SPORT),
        ("strength_low_hr.fit", ThresholdRejection.WRONG_SPORT),
        ("run_one_second.fit", ThresholdRejection.TOO_SHORT),
    ],
)
def test_the_older_fixtures_that_are_not_a_run_of_30_minutes_are_rejected(
    name: str, reason: ThresholdRejection
) -> None:
    assert evaluate_file(name) is reason


def test_a_steady_hour_without_distance_is_a_test_from_its_first_30_minutes() -> None:
    # The user chose it; nothing says it is not a test. A tie in mean HR goes to the first window.
    result = evaluate_file("run_constant_160.fit")

    assert isinstance(result, ThresholdTestResult)
    assert result.selection is SegmentSelection.BY_HEART_RATE
    assert result.segment_start_s == 0.0
    assert result.threshold_bpm == 160
