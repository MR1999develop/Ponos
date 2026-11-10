# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Heart-rate zones chosen by the user on 2026-11-10 (Z2 from 75%, Z3 from 85%, Z4 from 95%, Z5 from
103% of the threshold) on the real stack: the seconds per zone of imported runs, the load that
did not move, and the planned blocks that follow the same boundaries.

The load (Banister TRIMP and TRIMPeq) is written out by hand here and compared with what the
overview gives: changing the zones changed the seconds per zone and the planned load of a session,
never the load of a run that was done.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.load.domain.zones import ZONE_STARTS_PERCENT, zone_indices
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.planning.domain import Plan, PlanInputs, build_plan
from tests.collaudo.fase4_support import FIRST_DAY, START, Leg, run_fit
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import fit_writer as fw
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race

LIMITS = (50, 190)
RESERVE = LIMITS[1] - LIMITS[0]


def physiology_with(threshold: int) -> Physiology:
    return Physiology(HeartRateLimits(*LIMITS), ThresholdHeartRate(threshold), Sex.MALE)


@pytest.fixture
def lab(tmp_path: Path) -> Iterator[RealProfile]:
    profile = create_profile(tmp_path)
    profile.session.physiology.put(PhysiologyEntry(FIRST_DAY, physiology_with(100)))
    yield profile
    profile.close()


def only_activity(profile: RealProfile) -> ProfileActivity:
    [activity] = profile.overview().activities
    return activity


def import_legs(
    profile: RealProfile, legs: list[Leg], sport: fw.Sport = fw.Sport.RUNNING, day: int = 0
) -> None:
    """Imports a run on the `day`-th day after the standard start (each day its own file)."""
    folder = profile.folder.parent / "incoming"
    folder.mkdir(exist_ok=True)
    path = folder / f"run{day}.fit"
    path.write_bytes(run_fit(legs, START + timedelta(days=day), serial=900 + day, sport=sport))
    summary = profile.import_files([path])
    assert summary.results[0].issue is None


# --- Seconds per zone, by hand --------------------------------------------------------------------
def test_the_seconds_per_zone_follow_the_boundaries_of_the_user_on_both_sides(
    lab: RealProfile,
) -> None:
    # threshold 100: the boundaries are whole numbers (75, 85, 95, 103). One leg of 100 s on each
    # side of each boundary; the last record of the file lasts one more second.
    bpm_values = (74, 75, 84, 85, 94, 95, 102, 103)
    import_legs(lab, [Leg(100, bpm, 3.0) for bpm in bpm_values])

    zones = only_activity(lab).load.zone_seconds.values

    assert zones == (100.0, 200.0, 200.0, 200.0, 101.0)  # 74 | 75-84 | 85-94 | 95-102 | 103+


def test_the_boundaries_for_the_default_threshold_of_170_are_the_next_whole_bpm(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    profile.session.physiology.put(PhysiologyEntry(FIRST_DAY, physiology_with(170)))
    try:
        # 75% = 127.5 -> 128, 85% = 144.5 -> 145, 95% = 161.5 -> 162, 103% = 175.1 -> 176
        values = (127, 128, 144, 145, 161, 162, 175, 176)
        import_legs(profile, [Leg(50, bpm, 3.0) for bpm in values])

        zones = only_activity(profile).load.zone_seconds.values

        assert zones == (50.0, 100.0, 100.0, 100.0, 51.0)
    finally:
        profile.close()


def test_the_zone_edges_in_the_domain_are_the_percentages_chosen_by_the_user() -> None:
    assert ZONE_STARTS_PERCENT == (75, 85, 95, 103)
    for threshold in range(60, 221):
        bpm = np.arange(0, 251, dtype=np.float64)
        zone = zone_indices(bpm, threshold)
        for index, percent in enumerate(ZONE_STARTS_PERCENT, start=1):
            first = min(int(b) for b in bpm[zone >= index])
            assert first == math.ceil(percent * threshold / 100)  # the integer boundary, exactly
        assert np.all(np.diff(zone) >= 0)  # never a lower zone at a higher heart rate


def test_a_bike_ride_uses_the_other_sports_lt2_when_one_is_entered(lab: RealProfile) -> None:
    # running LT2 100, other-sports LT2 96: 99 is Z5, 98 is Z4 (103% of 96 = 98.88: Z5 from 99)
    lab.session.physiology.put(
        PhysiologyEntry(
            FIRST_DAY,
            Physiology(
                HeartRateLimits(*LIMITS),
                ThresholdHeartRate(100),
                Sex.MALE,
                other_threshold=ThresholdHeartRate(96),
            ),
        )
    )
    import_legs(lab, [Leg(100, 98, 8.0), Leg(100, 99, 8.0)], sport=fw.Sport.CYCLING)
    activity = only_activity(lab)

    assert activity.sport is Sport.CYCLING
    assert activity.load.zone_seconds.values == (0.0, 0.0, 0.0, 100.0, 101.0)


def test_a_bike_ride_without_an_other_sports_lt2_uses_the_running_one(lab: RealProfile) -> None:
    # running LT2 100: 95 and 96 are both Z4 (94% to 99%); before the two groups the bike used 96
    import_legs(lab, [Leg(100, 95, 8.0), Leg(100, 96, 8.0)], sport=fw.Sport.CYCLING)
    activity = only_activity(lab)

    assert activity.load.zone_seconds.values == (0.0, 0.0, 0.0, 201.0, 0.0)


# --- The load that was done did not move ----------------------------------------------------------
def banister(bpm: int, seconds: int, threshold: int) -> float:
    """TRIMPeq of a run at a constant `bpm` with `seconds` of 1 s samples, by hand."""
    k = 1.92

    def per_minute(value: float) -> float:
        hrr = (value - LIMITS[0]) / RESERVE
        return hrr * 0.64 * math.exp(k * hrr)

    trimp = seconds / 60.0 * per_minute(bpm)
    return 100.0 * trimp / (60.0 * per_minute(threshold))


@pytest.mark.parametrize("bpm", [70, 127, 128, 144, 145, 161, 162, 175, 176, 185])
def test_the_load_of_a_run_is_banister_by_hand_whatever_the_zone_it_sits_in(
    tmp_path: Path, bpm: int
) -> None:
    profile = create_profile(tmp_path)
    profile.session.physiology.put(PhysiologyEntry(FIRST_DAY, physiology_with(170)))
    try:
        import_legs(profile, [Leg(1800, bpm, 3.0)])  # 1801 records, 1 s each
        activity = only_activity(profile)
        expected = banister(bpm, 1801, 170)

        assert activity.load.trimp_eq == pytest.approx(expected, rel=1e-9)
    finally:
        profile.close()


def test_the_load_has_no_step_where_a_zone_begins(tmp_path: Path) -> None:
    profile = create_profile(tmp_path)
    profile.session.physiology.put(PhysiologyEntry(FIRST_DAY, physiology_with(170)))
    try:
        for day, bpm in enumerate((127, 128, 144, 145, 161, 162, 175, 176)):
            import_legs(profile, [Leg(600, bpm, 3.0)], day=day)
        loads = [a.load.trimp_eq for a in profile.overview().activities]
        # the ratio of the loads of two runs is the ratio of the TRIMP of their beats, as ever
        for (low, high), (a, b) in zip(
            ((127, 128), (144, 145), (161, 162), (175, 176)),
            (
                (loads[0], loads[1]),
                (loads[2], loads[3]),
                (loads[4], loads[5]),
                (loads[6], loads[7]),
            ),
            strict=True,
        ):
            assert b / a == pytest.approx(banister(high, 601, 170) / banister(low, 601, 170))
    finally:
        profile.close()


def test_the_weekly_seconds_per_zone_are_the_sum_of_the_runs(lab: RealProfile) -> None:
    folder = lab.folder.parent / "incoming"
    folder.mkdir(exist_ok=True)
    for number, (day, bpm) in enumerate(((0, 80), (1, 90), (2, 99)), start=1):  # Z2, Z3, Z4
        path = folder / f"r{number}.fit"
        data = run_fit([Leg(300, bpm, 3.0)], START + timedelta(days=day), serial=500 + number)
        path.write_bytes(data)
        assert lab.import_files([path]).results[0].issue is None

    overview = lab.overview()
    weekly = overview.weekly_zones(date(2026, 3, 20))

    assert len(weekly.weeks) == 2  # Saturday and Sunday, then Monday
    assert weekly.seconds.sum(axis=0).tolist() == [0.0, 301.0, 301.0, 301.0, 0.0]
    assert weekly.seconds[0].tolist() == [0.0, 301.0, 301.0, 0.0, 0.0]


# --- The plan follows the same boundaries ---------------------------------------------------------
def check_blocks(plan: Plan, threshold: int) -> None:
    seen: set[int] = set()
    for day in plan.days:
        if day.session is None:
            continue
        for block in day.session.blocks:
            seen.add(block.zone)
            inside = np.arange(block.hr_low_bpm, block.hr_high_bpm + 1, dtype=np.float64)
            assert set(zone_indices(inside, threshold).tolist()) == {block.zone - 1}, block
            assert block.hr_low_bpm <= block.hr_high_bpm
    assert seen  # the plan has sessions


@given(st.integers(95, 199))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_every_planned_block_stays_inside_its_own_zone_for_any_threshold(threshold: int) -> None:
    base = inputs([race("a", TODAY + timedelta(days=84), Priority.A, 21_097)], avail=BIG_WEEK)
    physiology = Physiology(HeartRateLimits(50, 200), ThresholdHeartRate(threshold), Sex.MALE)
    changed = PlanInputs(
        base.races, base.fitness_goals, base.availability, base.choices, physiology, base.fitness,
        threshold_pace_s_per_km=base.threshold_pace_s_per_km,
    )  # fmt: skip
    plan = build_plan(changed, TODAY)
    assert isinstance(plan, Plan)
    check_blocks(plan, threshold)


def test_the_zones_of_the_blocks_tile_the_heart_rate_range_without_gaps(tmp_path: Path) -> None:
    plan = build_plan(
        inputs([race("a", TODAY + timedelta(days=84), Priority.A, 21_097)], avail=BIG_WEEK), TODAY
    )
    assert isinstance(plan, Plan)
    ranges: dict[int, tuple[int, int]] = {}
    for day in plan.days:
        for block in day.session.blocks if day.session else ():
            ranges[block.zone] = (block.hr_low_bpm, block.hr_high_bpm)
    assert sorted(ranges) == [1, 2, 3, 4, 5]
    for lower, upper in zip(range(1, 5), range(2, 6), strict=True):
        assert ranges[lower][1] + 1 == ranges[upper][0]  # no beat belongs to two zones or none
    assert ranges[1][0] == 50 and ranges[5][1] == 190  # from the resting to the maximum heart rate
