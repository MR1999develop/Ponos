# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cumulative distance series: the domain type and what the FIT reader gives."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import DistanceSeries
from ponos_coach.activities.fit_decoder import decode_activity
from tests.fixtures.generators import fit_writer as fw

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)


# --- The type -------------------------------------------------------------------------------


def test_a_valid_series_is_kept_read_only_and_copied() -> None:
    seconds, meters = np.array([0.0, 1.0, 2.0]), np.array([0.0, 3.0, 6.0])

    series = DistanceSeries(seconds, meters)
    seconds[0] = 99.0

    assert series.seconds[0] == 0.0
    assert not series.seconds.flags.writeable
    assert not series.meters.flags.writeable
    assert len(series) == 3


def test_a_distance_that_stays_still_is_valid() -> None:
    assert len(DistanceSeries(np.array([0.0, 1.0]), np.array([5.0, 5.0]))) == 2


@pytest.mark.parametrize(
    ("seconds", "meters"),
    [
        ([], []),  # empty
        ([0.0, 1.0], [0.0]),  # different lengths
        ([0.0, 0.0], [0.0, 1.0]),  # seconds not strictly increasing
        ([1.0, 0.0], [0.0, 1.0]),  # seconds going backwards
        ([0.0, 1.0], [2.0, 1.0]),  # distance going backwards
        ([0.0, 1.0], [-1.0, 0.0]),  # negative distance
        ([0.0, float("nan")], [0.0, 1.0]),  # non-finite
        ([0.0, 1.0], [0.0, float("inf")]),
    ],
)
def test_an_invalid_series_is_refused(seconds: list[float], meters: list[float]) -> None:
    with pytest.raises(ValueError, match=r"series|distance|seconds|finite"):
        DistanceSeries(np.array(seconds), np.array(meters))


@given(st.lists(st.floats(0.0, 100.0), min_size=1, max_size=50))
def test_any_cumulative_sum_of_non_negative_steps_is_a_valid_series(steps: list[float]) -> None:
    meters = np.cumsum(steps)

    series = DistanceSeries(np.arange(len(steps), dtype=np.float64), meters)

    assert (np.diff(series.meters) >= 0).all()


# --- FIT -------------------------------------------------------------------------------------


def fit_run(samples: list[fw.Sample]) -> bytes:
    return fw.build_activity(
        fw.ActivitySpec(
            START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=7000001
        )
    )


def stamp(i: int) -> int:
    return fw.datetime_to_fit(START) + i


def test_fit_distance_is_read_with_the_time_axis_of_the_records() -> None:
    samples = [fw.Sample(stamp(i), 150, distance_m=3.0 * i) for i in range(10)]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert activity.distance.seconds.tolist() == list(range(10))
    assert activity.distance.meters == pytest.approx([3.0 * i for i in range(10)])


def test_fit_distance_may_cover_records_without_hr() -> None:
    samples = [fw.Sample(stamp(i), None if i % 2 else 150, distance_m=2.0 * i) for i in range(10)]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert len(activity.distance) == 10
    assert activity.heart_rate is not None
    assert len(activity.heart_rate) == 5


def test_fit_speed_is_integrated_when_there_is_no_distance_field() -> None:
    samples = [fw.Sample(stamp(i), 150, speed_ms=4.0) for i in range(11)]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert activity.distance.meters[0] == 0.0
    assert activity.distance.meters[-1] == pytest.approx(40.0)


def test_fit_speed_does_not_invent_distance_across_a_pause() -> None:
    # records at 0..4 s, then at 60 s: the 56 s gap is a pause, not 56 s of running
    times = [0, 1, 2, 3, 4, 60, 61]
    samples = [fw.Sample(stamp(t), 150, speed_ms=4.0) for t in times]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert activity.distance.meters[-1] == pytest.approx(4.0 * 4 + 4.0)  # 4 s + the last second


def test_fit_distance_wins_over_speed_when_both_are_there() -> None:
    samples = [fw.Sample(stamp(i), 150, distance_m=10.0 * i, speed_ms=1.0) for i in range(5)]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert activity.distance.meters[-1] == pytest.approx(40.0)


def test_fit_without_distance_or_speed_has_no_series() -> None:
    samples = [fw.Sample(stamp(i), 150) for i in range(5)]

    assert decode_activity(fit_run(samples)).distance is None


def test_fit_distance_that_goes_backwards_gives_no_series_and_the_activity_stays_valid() -> None:
    meters = [0.0, 10.0, 20.0, 5.0, 15.0]  # a counter reset: not repaired, not trusted
    samples = [fw.Sample(stamp(i), 150, distance_m=m) for i, m in enumerate(meters)]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is None
    assert activity.heart_rate is not None


def test_fit_records_with_repeated_times_keep_the_first_distance() -> None:
    samples = [
        fw.Sample(stamp(0), 150, distance_m=0.0),
        fw.Sample(stamp(1), 150, distance_m=3.0),
        fw.Sample(stamp(1), 150, distance_m=4.0),
        fw.Sample(stamp(2), 150, distance_m=6.0),
    ]

    activity = decode_activity(fit_run(samples))

    assert activity.distance is not None
    assert activity.distance.meters == pytest.approx([0.0, 3.0, 6.0])
