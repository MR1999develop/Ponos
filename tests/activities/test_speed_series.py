# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Smoothed speed from the cumulative distance: smoothing, pauses, stops, point cap, FIT."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    MAX_SPEED_POINTS,
    MIN_MOVING_SPEED_M_S,
    PAUSE_GAP_S,
    SPEED_WINDOW_S,
    DistanceSeries,
    SpeedSeries,
    smoothed_speed,
)
from ponos_coach.activities.fit_decoder import decode_activity
from tests.fixtures.generators import fit_writer as fw

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)


def steady(speed_m_s: float, seconds: int, *, step_s: float = 1.0) -> DistanceSeries:
    """A runner at a constant speed, one record every `step_s` seconds."""
    t = np.arange(0.0, seconds + step_s, step_s)
    return DistanceSeries(t, t * speed_m_s)


def speed_of(series: DistanceSeries) -> SpeedSeries:
    result = smoothed_speed(series)
    assert result is not None
    return result


# --- The type -------------------------------------------------------------------------------


def test_a_speed_series_is_copied_read_only_and_may_have_holes() -> None:
    seconds, speed = np.array([0.0, 1.0, 2.0]), np.array([3.0, np.nan, 3.0])

    series = SpeedSeries(seconds, speed)
    seconds[0] = 99.0

    assert series.seconds[0] == 0.0
    assert not series.meters_per_second.flags.writeable
    assert len(series) == 3


@pytest.mark.parametrize(
    ("seconds", "speed"),
    [
        ([], []),
        ([0.0, 1.0], [1.0]),
        ([1.0, 1.0], [1.0, 1.0]),  # seconds not strictly increasing
        ([0.0, 1.0], [-1.0, 1.0]),  # negative speed
        ([0.0, 1.0], [np.nan, np.nan]),  # nothing to draw
        ([0.0, 1.0], [np.inf, 1.0]),  # pace would be zero
        ([0.0, np.nan], [1.0, 1.0]),
    ],
)
def test_an_invalid_speed_series_is_refused(seconds: list[float], speed: list[float]) -> None:
    with pytest.raises(ValueError, match=r"series|seconds|speeds"):
        SpeedSeries(np.array(seconds), np.array(speed))


# --- Smoothing ------------------------------------------------------------------------------


def test_a_constant_speed_gives_that_speed_everywhere_edges_included() -> None:
    result = speed_of(steady(3.0, 600))

    assert result.meters_per_second == pytest.approx(3.0)
    assert result.seconds[0] == 0.0
    assert result.seconds[-1] == 600.0


def test_a_known_pace_round_trips_through_a_fit_file() -> None:
    """A run at 5:00 per km (3.3333 m/s) written to a FIT file and read back."""
    pace_s_per_km = 300.0
    speed = 1000.0 / pace_s_per_km
    samples = [
        fw.Sample(fw.datetime_to_fit(START) + i, 150, distance_m=round(speed * i, 2))
        for i in range(0, 901)
    ]
    spec = fw.ActivitySpec(START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    activity = decode_activity(fw.build_activity(spec))
    assert activity.distance is not None

    result = speed_of(activity.distance)

    assert 1000.0 / result.meters_per_second == pytest.approx(pace_s_per_km, abs=0.5)


def test_a_fit_file_with_only_a_speed_field_also_gives_the_speed() -> None:
    samples = [fw.Sample(fw.datetime_to_fit(START) + i, 150, speed_ms=2.5) for i in range(0, 301)]
    spec = fw.ActivitySpec(START, fw.Sport.CYCLING, fw.SubSport.ROAD, samples)
    activity = decode_activity(fw.build_activity(spec))
    assert activity.distance is not None  # the reader integrates the speed

    result = speed_of(activity.distance)

    assert result.meters_per_second == pytest.approx(2.5, rel=0.01)


def test_a_step_change_is_smoothed_over_the_window() -> None:
    t = np.arange(0.0, 201.0)
    meters = np.where(t < 100, 2.0 * t, 200.0 + 4.0 * (t - 100))
    result = speed_of(DistanceSeries(t, meters))
    at = {float(s): float(v) for s, v in zip(result.seconds, result.meters_per_second, strict=True)}

    assert at[50.0] == pytest.approx(2.0)  # a window away from the step
    assert at[150.0] == pytest.approx(4.0)
    assert at[100.0] == pytest.approx(3.0)  # half the window on each side
    assert 2.0 < at[90.0] < 3.0  # inside the window: a ramp, not a jump
    assert SPEED_WINDOW_S == 30.0


def test_a_one_second_jolt_is_flattened_by_the_window() -> None:
    t = np.arange(0.0, 121.0)
    meters = 3.0 * t
    meters[60:] += 12.0  # a 12 m jump in one second (a GPS glitch)
    result = speed_of(DistanceSeries(t, meters))

    peak = float(result.meters_per_second.max())
    assert peak == pytest.approx(3.0 + 12.0 / SPEED_WINDOW_S)  # spread over the window


# --- Pauses ---------------------------------------------------------------------------------


def _paused() -> DistanceSeries:
    """100 s at 3 m/s, a 60 s pause (the watch recorded nothing), 100 s at 3 m/s again."""
    first = np.arange(0.0, 101.0)
    second = np.arange(160.0, 261.0)
    t = np.concatenate((first, second))
    meters = np.concatenate((3.0 * first, 300.0 + 3.0 * (second - 160.0)))
    return DistanceSeries(t, meters)


def test_a_pause_is_a_hole_in_the_middle_of_it_so_no_line_crosses_it() -> None:
    result = speed_of(_paused())

    inside = (result.seconds > 100.0) & (result.seconds < 160.0)
    assert inside.sum() == 1
    assert np.isnan(result.meters_per_second[inside]).all()
    assert np.isfinite(result.meters_per_second[~inside]).all()


def test_the_window_never_reaches_over_a_pause() -> None:
    result = speed_of(_paused())

    assert result.meters_per_second[~np.isnan(result.meters_per_second)] == pytest.approx(3.0)


def test_an_interval_up_to_the_pause_limit_is_not_a_pause() -> None:
    t = np.arange(0.0, 301.0, PAUSE_GAP_S)  # one record every 10 s
    result = speed_of(DistanceSeries(t, 3.0 * t))

    assert len(result) == len(t)
    assert np.isfinite(result.meters_per_second).all()


# --- Stops ----------------------------------------------------------------------------------


def test_a_stop_is_a_hole_not_a_peak() -> None:
    """60 s at 3 m/s, 90 s standing still, 60 s at 3 m/s: the pace of the stop would be infinite."""
    t = np.arange(0.0, 211.0)
    meters = np.where(t < 60, 3.0 * t, np.where(t < 150, 180.0, 180.0 + 3.0 * (t - 150)))
    result = speed_of(DistanceSeries(t, meters))
    at = dict(zip(result.seconds.tolist(), result.meters_per_second.tolist(), strict=True))

    assert np.isnan(at[105.0])  # deep inside the stop
    assert at[30.0] == pytest.approx(3.0)
    assert at[180.0] == pytest.approx(3.0)
    assert np.isinf(result.meters_per_second).sum() == 0


def test_the_slowest_speed_that_counts_is_the_threshold() -> None:
    slow = MIN_MOVING_SPEED_M_S * 0.9
    fast = MIN_MOVING_SPEED_M_S * 1.1
    t = np.arange(0.0, 301.0)

    assert smoothed_speed(DistanceSeries(t, slow * t)) is None
    result = speed_of(DistanceSeries(t, fast * t))
    assert result.meters_per_second == pytest.approx(fast)


def test_nothing_to_draw_gives_none() -> None:
    still = DistanceSeries(np.arange(0.0, 60.0), np.zeros(60))
    one = DistanceSeries(np.array([0.0]), np.array([0.0]))

    assert smoothed_speed(still) is None
    assert smoothed_speed(one) is None


def test_the_limit_must_leave_room_for_the_edges() -> None:
    with pytest.raises(ValueError, match="limit"):
        smoothed_speed(steady(3.0, 60), limit=2)


# --- Point cap ------------------------------------------------------------------------------


def test_a_long_session_is_thinned_to_about_the_cap() -> None:
    series = steady(3.0, 60_000)  # 16 hours at 1 Hz

    result = speed_of(series)

    assert len(result) <= MAX_SPEED_POINTS
    assert result.seconds[0] == 0.0
    assert result.seconds[-1] == 60_000.0


def test_thinning_keeps_the_edges_of_a_stop_and_of_a_pause() -> None:
    """A short stop between two long runs must stay a gap after thinning."""
    run = np.arange(0.0, 20_000.0)
    stop = np.arange(20_000.0, 20_040.0)
    rest = np.arange(20_040.0, 40_000.0)
    t = np.concatenate((run, stop, rest))
    meters = np.concatenate(
        (3.0 * run, np.full(stop.size, 3.0 * 20_000.0), 3.0 * 20_000.0 + 3.0 * (rest - 20_040.0))
    )

    result = speed_of(DistanceSeries(t, meters))

    assert len(result) <= MAX_SPEED_POINTS
    stopped = (result.seconds > 20_010.0) & (result.seconds < 20_030.0)
    assert stopped.any()
    assert np.isnan(result.meters_per_second[stopped]).all()
    assert np.isfinite(result.meters_per_second[result.seconds < 19_900.0]).all()


@given(
    st.lists(st.floats(0.0, 8.0), min_size=2, max_size=400),
    st.integers(8, 200),
)
def test_any_series_gives_none_or_a_valid_capped_series(steps: list[float], limit: int) -> None:
    t = np.arange(len(steps), dtype=np.float64)
    series = DistanceSeries(t, np.cumsum(steps))

    result = smoothed_speed(series, limit=limit)

    if result is not None:
        assert (np.diff(result.seconds) > 0).all()
        finite = result.meters_per_second[np.isfinite(result.meters_per_second)]
        assert (finite >= MIN_MOVING_SPEED_M_S).all()
