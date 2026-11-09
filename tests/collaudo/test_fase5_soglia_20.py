# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06 for the two protocols, on the real stack: the 20-minute test of the 5th edition next
to the 30-minute one.

Every expected value is worked out by hand from the legs of the run: the threshold HR of the
20-minute test is the mean of the 20 minutes times 0.95 (half up), the pace is the mean pace plus 5%
of the time per kilometre (not of the speed). Borders: 19:59 and 20:00, and the same for 30. The
test is saved with its protocol, taken back exactly, and feeds the plan (pace, HR zones, retest
window).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from ponos_coach.assessment.domain import (
    NotYet,
    Overdue,
    Recommended,
    SegmentSelection,
    ThresholdProtocol,
    ThresholdRejection,
    ThresholdTestResult,
)
from ponos_coach.assessment.domain.pace_zones import pace_zone_indices
from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain.zones import zone_indices
from ponos_coach.planning.domain import (
    Plan,
    RetestInUnloadWeek,
    RetestNotPlaceable,
    RetestScheduled,
    WeekKind,
)
from tests.collaudo.fase4_support import (
    DAY,
    Leg,
    ThresholdProfile,
    make_threshold_profile,
    run_fit,
    standard_legs,
    wire,
)
from tests.collaudo.fase5_support import TODAY, World, local_start, make_world, race

TWENTY = ThresholdProtocol.TWENTY_MINUTES
THIRTY = ThresholdProtocol.THIRTY_MINUTES


@pytest.fixture
def tp(tmp_path: Path) -> Iterator[ThresholdProfile]:
    profile = make_threshold_profile(tmp_path)  # HR 50-190, threshold 170 from 2026-01-01
    yield profile
    profile.close()


def evaluate(
    tp: ThresholdProfile, data: bytes, protocol: ThresholdProtocol = TWENTY
) -> ThresholdTestResult | ThresholdRejection:
    outcome = tp.evaluate(tp.import_bytes(data).identity, protocol)
    assert isinstance(outcome, ThresholdTestResult | ThresholdRejection), outcome
    return outcome


def accepted(outcome: object) -> ThresholdTestResult:
    assert isinstance(outcome, ThresholdTestResult), outcome
    return outcome


def twenty_legs(*, hr: int = 160, speed: float = 3.5, seconds: int = 1200) -> list[Leg]:
    """Warm-up (easy, 10 min), the test, cool-down (easy, 5 min)."""
    return [Leg(600, 130, 3.0), Leg(seconds, hr, speed), Leg(300, 120, 2.5)]


# --- By hand --------------------------------------------------------------------------------------
def test_t_piano_06_the_20_minute_test_by_hand_hr_95_percent_pace_plus_5_percent_of_the_time(
    tp: ThresholdProfile,
) -> None:
    result = accepted(evaluate(tp, run_fit(twenty_legs(hr=160, speed=3.5))))

    assert result.protocol is TWENTY
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert result.segment_start_s == 600.0  # the 20 minutes after the warm-up
    assert result.threshold_bpm == 152  # 160 x 0.95
    assert result.pace_s_per_km == 300  # 285.714 s/km x 1.05; on the speed it would be 301
    assert result.valid_hr_samples == 1200
    assert result.hr_zone_starts_bpm == pytest.approx((152 * 0.78, 152 * 0.87, 152 * 0.94, 152.0))
    assert result.pace_zone_starts_s_per_km == pytest.approx(
        (300 / 0.78, 300 / 0.88, 300 / 0.94, 300.0), abs=1e-9
    )


@pytest.mark.parametrize(
    ("mean_bpm", "expected"),
    [
        (150, 143),  # 142.5: half up (a banker's rounding would say 142)
        (151, 143),  # 143.45
        (152, 144),  # 144.4
        (153, 145),  # 145.35
        (154, 146),  # 146.3
        (170, 162),  # 161.5: half up, and an even digit would say 162 too
        (110, 105),  # 104.5: half up (banker's: 104)
        (190, 181),  # 180.5, the maximum of the profile
        (54, 51),  # 51.3, the lowest accepted: above the minimum of 50
    ],
)
def test_t_piano_06_the_20_minute_hr_is_rounded_half_up_after_the_95_percent(
    tp: ThresholdProfile, mean_bpm: int, expected: int
) -> None:
    result = accepted(evaluate(tp, run_fit(twenty_legs(hr=mean_bpm))))

    assert result.threshold_bpm == expected


@pytest.mark.parametrize("mean_bpm", [53, 52, 50])
def test_t_piano_06_a_20_minute_threshold_at_or_below_the_minimum_is_out_of_range(
    tp: ThresholdProfile, mean_bpm: int
) -> None:
    # 53 x 0.95 = 50.35 -> 50, which is the minimum of the profile: not strictly above it
    assert evaluate(tp, run_fit(twenty_legs(hr=mean_bpm))) is (
        ThresholdRejection.THRESHOLD_OUT_OF_RANGE
    )


@pytest.mark.parametrize(
    ("speed", "pace"),
    [
        (3.5, 300),  # 285.714 x 1.05 = 300.0
        (4.0, 263),  # 250 x 1.05 = 262.5: half up
        (3.0, 350),  # 333.33 x 1.05 = 350.0
        (5.0, 210),  # 200 x 1.05
        (2.5, 420),  # 400 x 1.05
        (2.0, 525),  # 500 x 1.05
    ],
)
def test_t_piano_06_the_5_percent_is_on_the_time_per_kilometre(
    tp: ThresholdProfile, speed: float, pace: int
) -> None:
    result = accepted(evaluate(tp, run_fit([Leg(1200, 160, speed)])))  # no faster warm-up

    assert result.pace_s_per_km == pace


def test_t_piano_06_the_best_20_minutes_of_a_longer_run_are_chosen_by_distance(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(300, 140, 3.0), Leg(1200, 165, 3.8), Leg(600, 150, 3.0)]

    result = accepted(evaluate(tp, run_fit(legs)))

    assert result.segment_start_s == 300.0
    assert result.threshold_bpm == 157  # 165 x 0.95 = 156.75
    assert result.pace_s_per_km == 276  # 1000 / 3.8 = 263.158 x 1.05 = 276.3


def test_t_piano_06_a_tie_between_equal_windows_goes_to_the_first(
    tp: ThresholdProfile,
) -> None:
    legs = [Leg(300, 130, 3.0), Leg(300, 100, 3.8), Leg(1200, 165, 3.8)]

    result = accepted(evaluate(tp, run_fit(legs)))

    # the 3.8 m/s lasts 1500 s: every start from 300 to 600 covers the same distance, the first
    # one wins, so the 300 s at 100 bpm are inside the 20 minutes: (300 x 100 + 900 x 165) / 1200
    assert result.segment_start_s == 300.0
    assert result.threshold_bpm == 141  # 148.75 x 0.95 = 141.3


# --- The two protocols side by side ---------------------------------------------------------------
def test_t_piano_06_the_same_run_means_different_things_to_the_two_protocols(
    tp: ThresholdProfile,
) -> None:
    data = run_fit(standard_legs(hr=165, speed_ms=3.5))  # 30 minutes at 165 and 3.5 m/s

    thirty = accepted(evaluate(tp, data, THIRTY))
    twenty = accepted(tp.evaluate(tp.profile.records()[-1].identity, TWENTY))

    assert thirty.protocol is THIRTY
    assert (thirty.threshold_bpm, thirty.pace_s_per_km) == (165, 286)  # the last 20' as they are
    assert twenty.protocol is TWENTY
    assert (twenty.threshold_bpm, twenty.pace_s_per_km) == (157, 300)  # 165 x 0.95; 285.7 x 1.05


def test_t_piano_06_the_default_protocol_is_still_the_30_minute_one(tp: ThresholdProfile) -> None:
    identity = tp.import_bytes(run_fit(standard_legs(hr=165))).identity

    default = accepted(tp.evaluate(identity))

    assert default.protocol is THIRTY
    assert default.threshold_bpm == 165


# --- Borders: 19:59 / 20:00 and 29:59 / 30:00 -----------------------------------------------------
def test_t_piano_06_19_59_is_too_short_and_20_00_is_a_test(tp: ThresholdProfile) -> None:
    assert evaluate(tp, run_fit([Leg(1199, 160, 3.5)], serial=11)) is ThresholdRejection.TOO_SHORT
    result = accepted(evaluate(tp, run_fit([Leg(1200, 160, 3.5)], serial=12)))
    assert result.segment_start_s == 0.0
    assert (result.threshold_bpm, result.pace_s_per_km) == (152, 300)


def test_t_piano_06_the_records_of_19_59_inside_an_elapsed_time_of_20_00_are_too_short(
    tp: ThresholdProfile,
) -> None:
    data = run_fit([Leg(1199, 160, 3.5)], elapsed_s=1200.0, last_second=1198)

    assert evaluate(tp, data) is ThresholdRejection.TOO_SHORT


@pytest.mark.parametrize("protocol", [TWENTY, THIRTY])
def test_t_piano_06_each_protocol_has_its_own_border(
    tp: ThresholdProfile, protocol: ThresholdProtocol
) -> None:
    length = protocol.duration_s
    short = evaluate(tp, run_fit([Leg(length - 1, 160, 3.5)], serial=21), protocol)
    exact = evaluate(tp, run_fit([Leg(length, 160, 3.5)], serial=22), protocol)

    assert short is ThresholdRejection.TOO_SHORT
    assert isinstance(exact, ThresholdTestResult)


def test_t_piano_06_a_25_minute_run_is_a_test_of_20_and_not_of_30(tp: ThresholdProfile) -> None:
    data = run_fit([Leg(1500, 160, 3.5)])
    identity = tp.import_bytes(data).identity

    assert isinstance(tp.evaluate(identity, TWENTY), ThresholdTestResult)
    assert tp.evaluate(identity, THIRTY) is ThresholdRejection.TOO_SHORT


# --- The other rejections hold for the 20 minutes too ---------------------------------------------
def test_t_piano_06_a_pause_of_60_seconds_is_accepted_and_of_61_is_rejected_in_the_20_minutes(
    tp: ThresholdProfile,
) -> None:
    ok = run_fit(twenty_legs(), dropped=set(range(1000, 1059)), serial=31)  # records 60 s apart
    too_much = run_fit(twenty_legs(), dropped=set(range(1000, 1060)), serial=32)  # 61 s apart

    assert isinstance(evaluate(tp, ok), ThresholdTestResult)
    assert evaluate(tp, too_much) is ThresholdRejection.TOO_MANY_PAUSES


def test_t_piano_06_the_hr_coverage_of_the_20_minutes_is_90_percent_exactly(
    tp: ThresholdProfile,
) -> None:
    def legs(invalid: int) -> list[Leg]:
        return [Leg(600, 130, 3.0), Leg(invalid, 220, 3.5), Leg(1200 - invalid, 160, 3.5),
                Leg(300, 120, 2.5)]  # fmt: skip

    ok = accepted(evaluate(tp, run_fit(legs(120), serial=41)))
    assert ok.threshold_bpm == 152  # the invalid seconds are not part of the mean
    assert evaluate(tp, run_fit(legs(121), serial=42)) is (
        ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES
    )


def test_t_piano_06_a_20_minute_run_without_hr_cannot_be_the_test(tp: ThresholdProfile) -> None:
    legs = [Leg(1500, None, 3.5)]

    assert evaluate(tp, run_fit(legs)) is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_t_piano_06_a_gps_glitch_in_the_20_minutes_keeps_the_hr_and_drops_the_pace(
    tp: ThresholdProfile,
) -> None:
    data = run_fit(twenty_legs(), serial=51)
    # a run on a treadmill: no distance at all, the test goes by heart rate
    flat = run_fit(twenty_legs(), with_distance=False, serial=52)

    assert accepted(evaluate(tp, data)).pace_s_per_km == 300
    by_hr = accepted(evaluate(tp, flat))
    assert by_hr.pace_s_per_km is None
    assert by_hr.pace_zone_starts_s_per_km is None
    assert by_hr.threshold_bpm == 152
    assert by_hr.selection is SegmentSelection.BY_HEART_RATE


# --- Apply, protocol saved, exact undo ------------------------------------------------------------
def test_t_piano_06_applying_a_20_minute_test_saves_its_protocol_and_the_undo_is_exact(
    tp: ThresholdProfile,
) -> None:
    before = tp.entries()
    result = accepted(evaluate(tp, run_fit(twenty_legs(hr=160, speed=3.5))))

    applied = tp.apply(result)

    [saved] = tp.list_tests()
    assert saved.protocol is TWENTY
    assert (saved.threshold_bpm, saved.pace_s_per_km) == (152, 300)
    assert saved.test_day == DAY
    entries = {e.valid_from: e.physiology.threshold.bpm for e in tp.entries()}
    assert entries[DAY] == 152  # valid from the test day

    tp.remove(applied)

    assert tp.list_tests() == ()
    assert tp.entries() == before


def test_t_piano_06_a_30_minute_test_applied_next_to_a_20_minute_one_keeps_each_protocol(
    tp: ThresholdProfile,
) -> None:
    first = accepted(evaluate(tp, run_fit(twenty_legs(), serial=61)))
    second = accepted(
        evaluate(
            tp,
            run_fit(standard_legs(hr=168), local_start(date(2026, 3, 20), 7), serial=62),
            THIRTY,
        )
    )

    tp.apply(first)
    tp.apply(second)

    assert [t.protocol for t in tp.list_tests()] == [TWENTY, THIRTY]


# --- The plan uses the test -----------------------------------------------------------------------
@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    opened = make_world(tmp_path / "w")
    opened.import_history(TODAY - timedelta(days=30), TODAY - timedelta(days=1), every=3)
    opened.add_goal(race("a", TODAY + timedelta(days=100), Priority.A, 21_097))
    yield opened
    opened.close()


def test_t_piano_06_the_plan_takes_pace_and_zones_from_the_20_minute_test(world: World) -> None:
    lab = wire(world.profile, TODAY)
    test_day = TODAY - timedelta(days=5)
    data = run_fit(twenty_legs(hr=160, speed=3.5), local_start(test_day, 8), serial=71)
    result = accepted(lab.evaluate(lab.import_bytes(data).identity, TWENTY))
    lab.apply(result)

    plan = world.built().plan

    assert plan.inputs.threshold_pace_s_per_km == 300
    assert plan.inputs.physiology.threshold.bpm == 152
    blocks = [b for d in plan.days if d.session for b in d.session.blocks]
    assert len({b.zone for b in blocks}) >= 4
    for block in blocks:
        inside = np.arange(block.hr_low_bpm, block.hr_high_bpm + 1, dtype=np.float64)
        assert set(zone_indices(inside, 152).tolist()) == {block.zone - 1}, block
        if block.zone > 1 and block.hr_low_bpm > 50:
            below = np.array([block.hr_low_bpm - 1], dtype=np.float64)
            assert zone_indices(below, 152)[0] == block.zone - 2
        if block.pace_slow_s_per_km is not None and block.pace_fast_s_per_km is not None:
            paces = np.arange(
                block.pace_fast_s_per_km, block.pace_slow_s_per_km + 1, dtype=np.float64
            )
            assert set(pace_zone_indices(paces, 300).tolist()) == {block.zone - 1}, block


@pytest.mark.parametrize("days_ago", [20, 45, 70])
def test_t_piano_06_the_retest_goes_in_the_first_unload_week_that_touches_the_window(
    world: World, days_ago: int
) -> None:
    lab = wire(world.profile, TODAY)
    test_day = TODAY - timedelta(days=days_ago)
    data = run_fit(twenty_legs(), local_start(test_day, 8), serial=80 + days_ago)
    lab.apply(accepted(lab.evaluate(lab.import_bytes(data).identity, TWENTY)))
    status = lab.next_retest()
    assert isinstance(status, NotYet | Recommended | Overdue)  # one per case
    plan = world.built().plan
    window_first = test_day + timedelta(days=42)
    window_last = test_day + timedelta(days=56)
    start = TODAY + timedelta(days=1)
    first, last = (
        (start, plan.days[-1].day)
        if isinstance(status, Overdue)
        else (max(window_first, start), window_last)
    )
    expected = next(
        (
            w.week_start
            for w in plan.weeks
            if w.kind is WeekKind.UNLOAD
            and w.week_start + timedelta(days=6) >= first
            and w.week_start <= last
        ),
        None,
    )

    proposal = plan.retest_proposal

    if not isinstance(status, NotYet):
        # method 5 (ADR 015): a test that is due is a session of the plan, in its first week
        (scheduled,) = (d for d in plan.days if d.session and d.session.threshold_test)
        assert proposal == RetestScheduled(scheduled.day)
    elif expected is None:
        assert proposal == RetestNotPlaceable()
    else:
        assert proposal == RetestInUnloadWeek(expected)
        assert (
            plan.weeks[[w.week_start for w in plan.weeks].index(expected)].kind is WeekKind.UNLOAD
        )


def test_t_piano_06_without_a_test_the_plan_has_one_in_its_first_week(world: World) -> None:
    plan: Plan = world.built().plan

    # method 5 (ADR 015): the test is a session, no more "do it before the plan starts"
    assert isinstance(plan.retest_proposal, RetestScheduled)
    first_week_end = plan.days[0].day + timedelta(days=(6 - plan.days[0].day.weekday()))
    assert plan.days[0].day <= plan.retest_proposal.day <= first_week_end


def test_the_local_day_of_a_late_evening_test_is_the_day_in_the_profile_zone(
    tp: ThresholdProfile,
) -> None:
    start = local_start(date(2026, 3, 14), 23, 50)  # 22:50 UTC: still the 14th in Rome
    result = accepted(evaluate(tp, run_fit(twenty_legs(), start, serial=91)))

    assert result.test_day == date(2026, 3, 14)
