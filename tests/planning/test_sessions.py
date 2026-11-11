# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Session models: structure, zone ranges that agree with the existing zones, load that agrees
with the load model."""

from __future__ import annotations

import numpy as np
import pytest

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.assessment.domain import pace_zone_indices
from ponos_coach.goals.domain import SessionType
from ponos_coach.load.domain import (
    Physiology,
    ThresholdHeartRate,
    compute_activity_load,
    zone_indices,
)
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import Phase
from ponos_coach.planning.domain.sessions import (
    Shape,
    attach_ranges,
    raw_blocks,
    session_load,
    shape_options,
    zone_minutes,
    zone_ranges,
    zone_rates,
)
from tests.planning.builders import physiology

PHYSIOLOGY = physiology()  # rest 50, max 190, threshold 170
CONTINUOUS = [t for t in SessionType if (t, Phase.BUILD) not in defaults.INTERVAL_MODELS]
CONTINUOUS = [t for t in CONTINUOUS if t in defaults.MIN_MINUTES]


@pytest.mark.parametrize("session_type", CONTINUOUS)
def test_continuous_sessions_last_whole_steps_within_their_limits(
    session_type: SessionType,
) -> None:
    options = shape_options(session_type, Phase.BASE, 1000)
    minutes = [s.minutes for s in options]
    assert minutes[0] == defaults.MIN_MINUTES[session_type]
    assert minutes[-1] == defaults.MAX_MINUTES[session_type]
    assert all(m % 5 == 0 for m in minutes)
    assert minutes == sorted(set(minutes))


@pytest.mark.parametrize("session_type", CONTINUOUS)
def test_the_blocks_add_up_to_the_duration(session_type: SessionType) -> None:
    for shape in shape_options(session_type, Phase.BASE, 1000):
        blocks = raw_blocks(shape, Phase.BASE)
        assert sum(b.minutes * b.repeats for b in blocks) == shape.minutes
        assert all(b.minutes >= 1 for b in blocks)


@pytest.mark.parametrize(("session_type", "phase"), sorted(defaults.INTERVAL_MODELS, key=str))
def test_interval_sessions_alternate_work_and_rest(session_type: SessionType, phase: Phase) -> None:
    model = defaults.INTERVAL_MODELS[(session_type, phase)]
    options = shape_options(session_type, phase, 1000)
    assert [s.reps for s in options] == list(range(model.min_reps, model.max_reps + 1))
    for shape in options:
        blocks = raw_blocks(shape, phase)
        work = next(b for b in blocks if b.role.value == "work")
        rests = [b for b in blocks if b.role.value == "rest"]
        assert (work.repeats, work.zone) == (shape.reps, model.zone)
        assert rests[0].repeats == shape.reps - 1  # no rest after the last repetition
        assert (
            sum(b.minutes * b.repeats for b in blocks)
            == shape.minutes
            == model.total_minutes(shape.reps)
        )


def test_options_never_exceed_the_time_available() -> None:
    assert shape_options(SessionType.EASY, Phase.BASE, 19) == ()
    assert [s.minutes for s in shape_options(SessionType.EASY, Phase.BASE, 33)] == [20, 25, 30]
    assert shape_options(SessionType.THRESHOLD, Phase.BUILD, 42) == ()
    assert [s.minutes for s in shape_options(SessionType.THRESHOLD, Phase.BUILD, 60)][
        -1
    ] == 15 + 10 + 20 + 3


def test_the_easy_and_long_sessions_are_mostly_below_zone_three() -> None:
    easy = zone_minutes(raw_blocks(Shape(SessionType.EASY, 60, 0), Phase.BASE))
    long = zone_minutes(raw_blocks(Shape(SessionType.LONG, 100, 0), Phase.BASE))
    assert easy[2:] == (0, 0, 0)
    assert long == (0, 85, 15, 0, 0)


# --- Load ----------------------------------------------------------------------------------------
def test_load_per_minute_grows_with_the_zone() -> None:
    rates = zone_rates(PHYSIOLOGY, Sport.RUNNING)
    assert list(rates) == sorted(rates)
    assert rates[0] > 0


@pytest.mark.parametrize("zone", [1, 2, 3, 4, 5])
def test_an_hour_in_a_zone_is_the_trimp_eq_of_an_hour_at_its_representative_heart_rate(
    zone: int,
) -> None:
    percent = defaults.ZONE_REPRESENTATIVE_PERCENT[zone - 1]
    bpm = min(170 * percent / 100, 190.0)
    seconds = np.arange(0.0, 3600.0, 1.0)
    series = HeartRateSeries(seconds, np.full_like(seconds, bpm))
    measured = compute_activity_load(Sport.RUNNING, 3600.0, series, PHYSIOLOGY)
    rate = zone_rates(PHYSIOLOGY, Sport.RUNNING)[zone - 1]
    assert rate * 60 == pytest.approx(measured.trimp_eq, rel=1e-6)


def test_an_hour_at_the_threshold_is_about_a_hundred() -> None:
    # Z4 stands for 97% of the threshold, Z5 for 103%: the threshold itself lies between them
    rates = zone_rates(PHYSIOLOGY, Sport.RUNNING)
    assert rates[3] * 60 < 100 < rates[4] * 60


def test_session_load_is_minutes_in_zone_times_the_zone_rate() -> None:
    rates = (1.0, 2.0, 3.0, 4.0, 5.0)
    blocks = raw_blocks(Shape(SessionType.LONG, 100, 0), Phase.BASE)  # 85' in Z2, 15' in Z3
    assert session_load(blocks, rates) == pytest.approx(85 * 2 + 15 * 3)


# --- Zone ranges agree with the zones of the load and of the pace --------------------------------
@pytest.mark.parametrize("threshold", [150, 165, 170, 173])
def test_hr_ranges_are_the_zones_of_the_load(threshold: int) -> None:
    athlete = Physiology(PHYSIOLOGY.limits, ThresholdHeartRate(threshold), PHYSIOLOGY.sex)
    ranges = zone_ranges(athlete, Sport.RUNNING, None)
    for zone, (low, high) in enumerate(ranges.hr):
        ends = np.array([low, high], dtype=np.float64)
        assert list(zone_indices(ends, threshold)) == [zone, zone]
        if zone < len(ranges.hr) - 1:
            assert high + 1 == ranges.hr[zone + 1][0]  # no bpm falls between two zones


def test_hr_ranges_cover_from_rest_to_max() -> None:
    ranges = zone_ranges(PHYSIOLOGY, Sport.RUNNING, 300)
    assert ranges.hr[0][0] == 50
    assert ranges.hr[4][1] == 190


@pytest.mark.parametrize("pace", [240, 285, 300, 333])
def test_pace_ranges_are_the_pace_zones(pace: int) -> None:
    ranges = zone_ranges(PHYSIOLOGY, Sport.RUNNING, pace).pace
    assert ranges[0][0] is None
    assert ranges[4][1] is None
    for zone, (slow, fast) in enumerate(ranges):
        for edge in (slow, fast):
            if edge is not None:
                assert pace_zone_indices(np.array([float(edge)]), pace)[0] == zone


def test_pace_ranges_are_absent_without_a_pace_or_off_running() -> None:
    assert zone_ranges(PHYSIOLOGY, Sport.RUNNING, None).pace == ((None, None),) * 5
    assert zone_ranges(PHYSIOLOGY, Sport.CYCLING, 300).pace == ((None, None),) * 5


def test_blocks_get_the_range_of_their_zone() -> None:
    ranges = zone_ranges(PHYSIOLOGY, Sport.RUNNING, 300)
    blocks = attach_ranges(raw_blocks(Shape(SessionType.LONG, 100, 0), Phase.BASE), ranges)
    assert [(b.zone, b.hr_low_bpm, b.hr_high_bpm) for b in blocks] == [
        (2, ranges.hr[1][0], ranges.hr[1][1]),
        (3, ranges.hr[2][0], ranges.hr[2][1]),
    ]
