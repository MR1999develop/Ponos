# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-02/03: how a plan distributes its intensity (80/20 by time and by sessions, hard
days), the stepped taper and the race-pace work of the peak and the taper."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

import pytest

from ponos_coach.goals.domain import INTENSE_TYPES, Priority, SessionType
from ponos_coach.planning.domain import Phase, Plan, PlanDay, WeekKind, build_plan, defaults
from ponos_coach.planning.domain.sessions import Shape
from ponos_coach.planning.domain.week import Placed, WeekSetup, week_ok
from tests.planning.builders import BIG_WEEK, TODAY, availability, inputs, race
from tests.planning.invariants import violations

MARATHON = 42_195
HALF = 21_097


def _plan(*args: object, **kwargs: object) -> Plan:
    result = build_plan(inputs(*args, **kwargs), TODAY)  # type: ignore[arg-type]
    assert isinstance(result, Plan)
    assert violations(result) == []
    return result


def _weeks_ahead(weeks: int) -> date:
    return TODAY + timedelta(weeks=weeks)


def _sessions(days: Sequence[PlanDay]) -> list[PlanDay]:
    return [d for d in days if d.session]


def _of_phase(plan: Plan, phase: Phase) -> list[PlanDay]:
    return [d for d in plan.days if d.phase is phase]


def _steps(plan: Plan, steps: int) -> list[float]:
    """Session load of each week of the taper, counted back from the race and listed from the
    first to the last, as a share of the load of the last 7 days of the peak."""
    race_day = plan.days[-1].day
    reference = sum(d.session.load for d in _of_phase(plan, Phase.PEAK)[-7:] if d.session)
    loads = []
    for step in range(steps - 1, -1, -1):
        days = [
            d for d in plan.days if (race_day - d.day).days - 1 in range(7 * step, 7 * step + 7)
        ]
        loads.append(sum(d.session.load for d in days if d.session) / reference)
    return loads


# --- Stepped taper (KNO24, HAU22) ----
@pytest.mark.parametrize(
    ("distance", "weeks_out", "windows"),
    [
        (MARATHON, 24, [(0.66, 0.78), (0.52, 0.65), (0.40, 0.52)]),  # -25%, ..., -50%
        (HALF, 20, [(0.65, 0.78), (0.40, 0.56)]),  # -25%, then -45%
        (10_000, 16, [(0.45, 0.62)]),  # -40%
    ],
    ids=["marathon", "half", "10k"],
)
def test_the_taper_steps_down_week_by_week(
    distance: int, weeks_out: int, windows: list[tuple[float, float]]
) -> None:
    plan = _plan([race("a", _weeks_ahead(weeks_out), distance_m=distance)], avail=BIG_WEEK, ctl=40)
    taper = _of_phase(plan, Phase.TAPER)
    assert len(taper) == 7 * len(windows)
    for share, (low, high) in zip(_steps(plan, len(windows)), windows, strict=True):
        assert low <= share <= high


def test_a_marathon_taper_lasts_three_weeks_a_half_two_a_10k_one() -> None:
    days = {}
    for distance in (MARATHON, HALF, 10_000):
        plan = _plan([race("a", _weeks_ahead(24), distance_m=distance)], avail=BIG_WEEK)
        days[distance] = len(_of_phase(plan, Phase.TAPER))
    assert days == {MARATHON: 21, HALF: 14, 10_000: 7}


def test_the_taper_keeps_its_sessions_even_from_no_fitness() -> None:
    # nothing to take a share of (no reference load): the days still have their sessions
    plan = _plan([race("a", _weeks_ahead(3))], avail=BIG_WEEK, ctl=0, atl=0)
    taper = _of_phase(plan, Phase.TAPER)
    assert len(taper) == 7
    assert all(d.session is not None for d in taper)


def test_the_taper_keeps_the_number_of_sessions_of_the_week_before() -> None:
    plan = _plan([race("a", _weeks_ahead(24), distance_m=MARATHON)], avail=BIG_WEEK, ctl=40)
    sessions_per_week = {w.week_start: w.session_count for w in plan.weeks}
    kinds = {w.week_start: w.kind for w in plan.weeks}
    reference = max(c for s, c in sessions_per_week.items() if kinds[s] is WeekKind.PEAK)
    taper_weeks = [c for s, c in sessions_per_week.items() if kinds[s] is WeekKind.TAPER]
    assert len(taper_weeks) == 3
    assert taper_weeks == [reference] * 3


def test_the_taper_has_one_shortened_quality_session_a_week_at_race_pace() -> None:
    plan = _plan(
        [race("a", _weeks_ahead(24), distance_m=MARATHON, target_time_s=12_600)],
        avail=BIG_WEEK,
        ctl=40,
    )
    race_day = plan.days[-1].day
    for week in (w for w in plan.weeks if w.kind is WeekKind.TAPER):
        end = week.week_start + timedelta(days=7)
        quality = [
            d for d in _sessions(plan.days) if week.week_start <= d.day < end and _intense(d)
        ]
        assert len(quality) == 1
        day = quality[0]
        assert day.session is not None
        assert day.session.session_type is SessionType.MEDIUM
        assert day.session.minutes <= defaults.TAPER_QUALITY_MAX_MINUTES
        assert day.session.race_pace
        assert (race_day - day.day).days > 3


def test_a_10k_taper_has_a_short_threshold_session_four_or_more_days_before() -> None:
    plan = _plan([race("a", _weeks_ahead(16))], avail=BIG_WEEK, ctl=40)
    quality = [d for d in _of_phase(plan, Phase.TAPER) if _intense(d)]
    assert len(quality) == 1
    session = quality[0].session
    assert session is not None
    assert session.session_type is SessionType.THRESHOLD
    assert session.minutes <= defaults.TAPER_QUALITY_MAX_MINUTES
    assert (plan.days[-1].day - quality[0].day).days >= 4


def _intense(day: PlanDay) -> bool:
    return day.session is not None and day.session.session_type in INTENSE_TYPES


@pytest.mark.parametrize("weekday", range(7))
def test_no_intense_session_in_the_72_hours_before_any_race(weekday: int) -> None:
    c_race = _weeks_ahead(10) + timedelta(days=weekday)
    plan = _plan(
        [race("a", _weeks_ahead(20)), race("c", c_race, Priority.C, 5_000)], avail=BIG_WEEK
    )
    by_day = {d.day: d for d in plan.days}
    for race_day in (c_race, _weeks_ahead(20)):
        assert not any(_intense(by_day[race_day - timedelta(days=n)]) for n in (1, 2, 3))


def test_the_unload_weeks_of_the_3_to_1_cycle_are_70_percent() -> None:
    plan = _plan([race("a", _weeks_ahead(30))], avail=BIG_WEEK, ctl=40, atl=40)
    assert dict(plan.parameters)["unload_fraction"] == 0.70
    loads = {w.week_start: w.load for w in plan.weeks}
    ratios = [
        loads[w.week_start] / loads[w.week_start - timedelta(days=7)]
        for w in plan.weeks
        if w.kind is WeekKind.UNLOAD
    ]
    assert len(ratios) >= 3
    assert sum(ratios) / len(ratios) >= 0.64  # the 65% of before was about 0.60
    assert max(ratios) <= 0.72


# --- 80/20 by sessions, hard days ----
def test_at_most_a_quarter_of_the_sessions_of_a_week_are_intense() -> None:
    plan = _plan([race("a", _weeks_ahead(20), distance_m=HALF)], avail=BIG_WEEK, ctl=40)
    full = [w for w in plan.weeks if w.session_count >= 7]
    assert full
    assert all(w.intense_session_share <= 0.25 for w in plan.weeks)
    assert any(w.intense_session_share > 0 for w in full)


@pytest.mark.parametrize(
    "minutes",
    [(60, 0, 60, 0, 60, 0, 0), (60, 0, 60, 0, 60, 0, 90), (60, 0, 0, 0, 0, 0, 90)],
    ids=["3 days", "4 days", "2 days"],
)
def test_a_week_of_three_or_four_sessions_has_one_intense_session(
    minutes: tuple[int, int, int, int, int, int, int],
) -> None:
    avail = availability(minutes, long_day=6 if minutes[6] else 4, max_week_minutes=600)
    plan = _plan([race("a", _weeks_ahead(16))], avail=avail)
    full = [w for w in plan.weeks if w.kind is WeekKind.BUILD]
    assert full
    sessions = len([m for m in minutes if m])
    for week in full:
        assert week.session_count == sessions
        intense = round(week.intense_session_share * week.session_count)
        assert intense == (1 if sessions >= 3 else 0)


def test_the_weeks_expose_both_shares() -> None:
    plan = _plan([race("a", _weeks_ahead(20), distance_m=HALF)], avail=BIG_WEEK, ctl=40)
    week = next(w for w in plan.weeks if w.kind is WeekKind.BUILD and w.session_count == 7)
    assert week.intense_session_share == pytest.approx(1 / 7, abs=1e-3)
    assert 0.8 <= week.low_zone_share < 1.0


def _week_of(types: Sequence[SessionType], phase: Phase = Phase.BUILD) -> dict[date, Placed]:
    return {TODAY + timedelta(days=i): Placed(Shape(t, 45, 0), phase) for i, t in enumerate(types)}


def _setup(phase: Phase = Phase.BUILD) -> WeekSetup:
    return WeekSetup((), phase, False, 6, frozenset(SessionType), frozenset())


def test_a_week_is_refused_with_more_than_three_hard_days() -> None:
    easy = [SessionType.EASY] * 16
    four_hard = [SessionType.LONG, SessionType.MEDIUM, SessionType.THRESHOLD, SessionType.LONG]
    assert week_ok(_setup(), _week_of(easy + four_hard[:3]))
    assert not week_ok(_setup(), _week_of(easy + four_hard))


def test_few_sessions_take_at_most_two_intense_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    # the share limit alone would already refuse this week; take it away to see the second rule
    monkeypatch.setattr(defaults, "MAX_INTENSE_SESSION_SHARE_PERCENT", 100)
    types = [SessionType.THRESHOLD, SessionType.VO2MAX, SessionType.MEDIUM, SessionType.EASY]
    assert not week_ok(_setup(), _week_of(types))
    assert week_ok(_setup(), _week_of(types[:2] + [SessionType.EASY] * 4))


# --- Peak: race-pace work (MUN15) ----
def _peak_quality(plan: Plan) -> list[PlanDay]:
    return [d for d in _of_phase(plan, Phase.PEAK) if _intense(d)]


def test_no_vo2max_in_the_peak_of_a_marathon_or_a_half() -> None:
    for distance in (MARATHON, HALF):
        plan = _plan([race("a", _weeks_ahead(24), distance_m=distance)], avail=BIG_WEEK)
        quality = _peak_quality(plan)
        assert quality
        assert SessionType.VO2MAX not in {d.session.session_type for d in quality if d.session}


def test_the_marathon_peak_rehearses_marathon_pace_in_the_medium_session() -> None:
    plan = _plan(
        [race("a", _weeks_ahead(24), distance_m=MARATHON, target_time_s=12_600)], avail=BIG_WEEK
    )
    for day in _peak_quality(plan):
        assert day.session is not None
        assert day.session.session_type is SessionType.MEDIUM
        assert day.session.race_pace
        paced = [b for b in day.session.blocks if b.race_pace_s_per_km is not None]
        assert [(b.zone, b.race_pace_s_per_km) for b in paced] == [(3, 299)]  # 12 600 s / 42.195 km


def test_the_half_marathon_peak_rehearses_its_pace_in_the_threshold_session() -> None:
    plan = _plan(
        [race("a", _weeks_ahead(24), distance_m=HALF, target_time_s=6_300)], avail=BIG_WEEK
    )
    for day in _peak_quality(plan):
        assert day.session is not None
        assert day.session.session_type is SessionType.THRESHOLD
        assert day.session.race_pace
        paced = [b for b in day.session.blocks if b.race_pace_s_per_km is not None]
        assert [(b.zone, b.race_pace_s_per_km) for b in paced] == [(4, 299)]  # 6 300 s / 21.097 km


def test_without_a_threshold_pace_or_a_target_the_session_goes_by_its_zone() -> None:
    plan = _plan([race("a", _weeks_ahead(24), distance_m=MARATHON)], avail=BIG_WEEK, pace=None)
    sessions = [d.session for d in _peak_quality(plan) if d.session]
    assert sessions
    assert all(s.race_pace for s in sessions)
    assert all(b.race_pace_s_per_km is None for s in sessions for b in s.blocks)


def test_the_peak_uses_threshold_if_the_medium_session_is_not_chosen() -> None:
    types = set(SessionType) - {SessionType.MEDIUM}
    plan = _plan([race("a", _weeks_ahead(24), distance_m=MARATHON)], avail=BIG_WEEK, types=types)
    sessions = [d.session for d in _peak_quality(plan) if d.session]
    assert {s.session_type for s in sessions} == {SessionType.THRESHOLD}
    assert not any(s.race_pace for s in sessions)  # threshold is not marathon pace


def test_the_peak_of_a_10k_alternates_vo2max_and_race_pace_threshold() -> None:
    plan = _plan([race("a", _weeks_ahead(24))], avail=BIG_WEEK)
    sessions = [d.session for d in _peak_quality(plan) if d.session]
    assert [s.session_type for s in sessions] == [SessionType.VO2MAX, SessionType.THRESHOLD] or [
        s.session_type for s in sessions
    ] == [SessionType.THRESHOLD, SessionType.VO2MAX]
    by_type = {s.session_type: s for s in sessions}
    assert by_type[SessionType.THRESHOLD].race_pace
    assert not by_type[SessionType.VO2MAX].race_pace
