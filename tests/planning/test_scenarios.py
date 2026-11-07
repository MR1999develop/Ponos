# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-03: the scenarios of the plan, one by one, in the language of the domain."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from ponos_coach.assessment.domain import NotYet, Overdue, Recommended
from ponos_coach.goals.domain import Priority, SessionType
from ponos_coach.planning.domain import (
    CloseARaces,
    CtlTargetNotReached,
    GoalBeyondHorizon,
    LongDayUnavailable,
    MissingIntenseSessions,
    MissingLongSession,
    Phase,
    Plan,
    PlanRefusal,
    RefusalReason,
    RetestBeforePlan,
    RetestInUnloadWeek,
    RetestNotPlaceable,
    WeekKind,
    build_plan,
)
from tests.planning.builders import (
    ALL_TYPES,
    BIG_WEEK,
    TODAY,
    availability,
    fitness_goal,
    inputs,
    race,
)
from tests.planning.invariants import violations


def _plan(*args: object, **kwargs: object) -> Plan:
    result = build_plan(inputs(*args, **kwargs), TODAY)  # type: ignore[arg-type]
    assert isinstance(result, Plan)
    assert violations(result) == []
    return result


def _weeks_ahead(weeks: int) -> date:
    return TODAY + timedelta(weeks=weeks)


def _phases(plan: Plan) -> dict[Phase, int]:
    counts: dict[Phase, int] = {}
    for day in plan.days:
        counts[day.phase] = counts.get(day.phase, 0) + 1
    return counts


def _types(plan: Plan) -> set[SessionType]:
    return {d.session.session_type for d in plan.days if d.session}


# --- One A race ----------------------------------------------------------------------------------
def test_one_a_race_is_built_backwards_from_its_date() -> None:
    plan = _plan([race("a", _weeks_ahead(30))], avail=BIG_WEEK, ctl=30, atl=30)
    race_day = _weeks_ahead(30)
    by_day = {d.day: d for d in plan.days}
    assert by_day[race_day].phase is Phase.RACE
    assert by_day[race_day].race is not None
    assert [by_day[race_day - timedelta(days=n)].phase for n in (1, 7)] == [Phase.TAPER] * 2
    assert by_day[race_day - timedelta(days=8)].phase is Phase.PEAK
    counts = _phases(plan)
    assert (counts[Phase.TAPER], counts[Phase.PEAK]) == (7, 14)
    assert (counts[Phase.BUILD], counts[Phase.BASE]) == (56, 84)  # 8 and 12 weeks
    assert counts[Phase.MAINTENANCE] == 30 * 7 - 1 - 7 - 14 - 56 - 84  # what is left before
    assert plan.days[-1].day == race_day


def test_taper_is_longer_for_longer_races_and_less_load_than_before() -> None:
    plan = _plan([race("a", _weeks_ahead(24), distance_m=42_195)], avail=BIG_WEEK, ctl=40, atl=40)
    taper = [d for d in plan.days if d.phase is Phase.TAPER]
    assert len(taper) == 21
    before = [d for d in plan.days if d.phase is Phase.PEAK][-7:]
    assert sum(d.load for d in taper) / 21 <= 0.7 * sum(d.load for d in before) / 7
    easy_or_quality = {SessionType.EASY, SessionType.MEDIUM}
    assert all(d.session is None or d.session.session_type in easy_or_quality for d in taper)


def test_ctl_climbs_to_the_target_when_it_is_reachable() -> None:
    plan = _plan([race("a", _weeks_ahead(20), distance_m=21_097)], avail=BIG_WEEK, ctl=30, atl=30)
    assert not any(isinstance(w, CtlTargetNotReached) for w in plan.warnings)
    peak_start = next(
        p for d, p in zip(plan.days, plan.projection, strict=True) if d.phase is Phase.PEAK
    )
    assert peak_start.ctl >= 55.0 - 5.0


def test_a_race_far_away_starts_with_maintenance() -> None:
    plan = _plan([race("a", _weeks_ahead(40))], avail=BIG_WEEK, ctl=35, atl=35)
    assert plan.days[0].phase is Phase.MAINTENANCE
    assert plan.weeks[0].kind is WeekKind.MAINTENANCE


def test_race_with_too_little_time_has_no_block() -> None:
    plan = _plan([race("a", _weeks_ahead(2))], avail=BIG_WEEK, ctl=30, atl=30)
    counts = _phases(plan)
    assert Phase.BASE not in counts and Phase.BUILD not in counts and Phase.PEAK not in counts
    assert counts[Phase.TAPER] == 7


def test_the_base_is_shortened_first_then_the_build() -> None:
    # 12 weeks: after the 7 taper days 76 free days = 14 peak + 56 build + 6 base
    counts = _phases(_plan([race("a", _weeks_ahead(12))], avail=BIG_WEEK))
    assert (counts[Phase.PEAK], counts[Phase.BUILD], counts[Phase.BASE]) == (14, 56, 6)
    # 6 weeks: 34 free days = 14 peak + 20 build, no base
    counts = _phases(_plan([race("a", _weeks_ahead(6))], avail=BIG_WEEK))
    assert (counts[Phase.PEAK], counts[Phase.BUILD], Phase.BASE in counts) == (14, 20, False)


# --- Two A races three weeks apart ---------------------------------------------------------------
def test_two_a_races_three_weeks_apart_have_only_recovery_and_maintenance_between() -> None:
    first, second = _weeks_ahead(12), _weeks_ahead(15)
    plan = _plan([race("a1", first), race("a2", second)], avail=BIG_WEEK, ctl=40, atl=40)
    between = [d for d in plan.days if first < d.day < second]
    assert {d.phase for d in between} == {Phase.RECOVERY, Phase.MAINTENANCE, Phase.TAPER}
    assert [
        d.phase
        for d in plan.days
        if d.day in (first - timedelta(days=1), second - timedelta(days=1))
    ] == [
        Phase.TAPER,
        Phase.TAPER,
    ]
    assert CloseARaces("a1", "a2", 21) in plan.warnings


def test_a_races_far_enough_apart_get_a_block_each() -> None:
    plan = _plan([race("a1", _weeks_ahead(10)), race("a2", _weeks_ahead(24))], avail=BIG_WEEK)
    assert not any(isinstance(w, CloseARaces) for w in plan.warnings)
    between = {d.phase for d in plan.days if _weeks_ahead(10) < d.day < _weeks_ahead(24)}
    assert {Phase.RECOVERY, Phase.BUILD, Phase.PEAK, Phase.TAPER} <= between


# --- A + B + C -----------------------------------------------------------------------------------
def test_b_race_has_a_mini_unload_and_c_race_has_none() -> None:
    a_day, b_day, c_day = _weeks_ahead(20), _weeks_ahead(10), _weeks_ahead(6)
    plan = _plan(
        [
            race("a", a_day),
            race("b", b_day, Priority.B, 10_000),
            race("c", c_day, Priority.C, 5_000),
        ],
        avail=BIG_WEEK,
        ctl=40,
        atl=40,
    )
    by_day = {d.day: d for d in plan.days}
    assert [by_day[b_day - timedelta(days=n)].phase for n in (1, 2, 3, 4)] == [Phase.MINI_TAPER] * 4
    assert by_day[b_day - timedelta(days=5)].phase is not Phase.MINI_TAPER
    assert by_day[c_day].phase is Phase.RACE
    assert by_day[c_day - timedelta(days=1)].phase not in (Phase.MINI_TAPER, Phase.TAPER)
    for goal_day in (a_day, b_day, c_day):
        assert by_day[goal_day].race is not None
        assert by_day[goal_day].session is None


def test_races_carry_the_time_and_load_the_plan_expects() -> None:
    plan = _plan([race("a", _weeks_ahead(10), target_time_s=3000)], avail=BIG_WEEK)
    planned = next(d.race for d in plan.days if d.race)
    assert planned is not None
    assert planned.expected_time_s == 3000
    assert planned.load > 0


# --- Unreachable goals ---------------------------------------------------------------------------
def test_unreachable_ctl_target_warns_and_keeps_the_ramp() -> None:
    plan = _plan([race("a", _weeks_ahead(8), distance_m=42_195)], avail=BIG_WEEK, ctl=0, atl=0)
    warning = next(w for w in plan.warnings if isinstance(w, CtlTargetNotReached))
    assert warning.goal_id == "a"
    assert warning.projected_ctl < warning.target_ctl - 5
    # the ramp invariant (checked by `_plan`) also held while pushing as hard as allowed
    assert max(p.ctl for p in plan.projection) < warning.target_ctl


def test_unreachable_target_time_warns() -> None:
    plan = _plan([race("a", _weeks_ahead(8), target_time_s=1500)], avail=BIG_WEEK, pace=300)
    assert plan.targets[0].classification.value == "unreachable"


# --- Refusals ------------------------------------------------------------------------------------
@pytest.mark.parametrize("offset", [0, -1, -400])
def test_a_goal_today_or_in_the_past_is_refused(offset: int) -> None:
    result = build_plan(inputs([race("old", TODAY + timedelta(days=offset))]), TODAY)
    assert result == PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("old",))


def test_a_goal_tomorrow_is_planned() -> None:
    plan = _plan([race("soon", TODAY + timedelta(days=1))])
    assert len(plan.days) == 1
    assert plan.days[0].race is not None


def test_a_past_fitness_goal_is_refused_too() -> None:
    result = build_plan(inputs([race("a", _weeks_ahead(5))], [fitness_goal("f", TODAY, 50)]), TODAY)
    assert result == PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("f",))


def test_no_goals_is_refused() -> None:
    assert build_plan(inputs(), TODAY) == PlanRefusal(RefusalReason.NO_GOALS)


@pytest.mark.parametrize(
    "avail",
    [availability((0,) * 7, 6, 600), availability((60,) * 7, 6, 0)],
    ids=["no day", "no hours"],
)
def test_no_availability_means_no_plan(avail: object) -> None:
    result = build_plan(inputs([race("a", _weeks_ahead(5))], avail=avail), TODAY)  # type: ignore[arg-type]
    assert result == PlanRefusal(RefusalReason.NO_AVAILABILITY)


def test_two_races_on_one_day_are_refused() -> None:
    day = _weeks_ahead(5)
    result = build_plan(inputs([race("b", day), race("a", day, Priority.C)]), TODAY)
    assert result == PlanRefusal(RefusalReason.GOALS_ON_SAME_DAY, ("a", "b"))


def test_no_session_type_for_the_sport_is_refused() -> None:
    result = build_plan(inputs([race("a", _weeks_ahead(5))], types=[]), TODAY)
    assert result == PlanRefusal(RefusalReason.NO_SESSION_TYPES)


# --- Session types: only the chosen ones, with a warning for what is missing ---------------------
def test_half_marathon_without_long_sessions_warns_and_adds_none() -> None:
    types = ALL_TYPES - {SessionType.LONG}
    plan = _plan([race("a", _weeks_ahead(12), distance_m=21_097)], types=types, avail=BIG_WEEK)
    assert MissingLongSession("a") in plan.warnings
    assert SessionType.LONG not in _types(plan)


def test_a_10k_without_long_sessions_has_no_warning() -> None:
    types = ALL_TYPES - {SessionType.LONG}
    plan = _plan([race("a", _weeks_ahead(12))], types=types, avail=BIG_WEEK)
    assert not any(isinstance(w, MissingLongSession) for w in plan.warnings)


def test_race_without_threshold_or_vo2max_warns_and_adds_none() -> None:
    types = {SessionType.EASY, SessionType.LONG}
    plan = _plan([race("a", _weeks_ahead(12))], types=types, avail=BIG_WEEK)
    assert MissingIntenseSessions("a") in plan.warnings
    assert _types(plan) <= types


def test_one_of_the_two_intense_types_is_enough_for_no_warning() -> None:
    types = {SessionType.EASY, SessionType.THRESHOLD}
    plan = _plan([race("a", _weeks_ahead(12))], types=types, avail=BIG_WEEK)
    assert not any(isinstance(w, MissingIntenseSessions) for w in plan.warnings)
    assert SessionType.THRESHOLD in _types(plan)


def test_a_c_race_asks_for_nothing() -> None:
    types = {SessionType.EASY}
    plan = _plan([race("c", _weeks_ahead(12), Priority.C, 21_097)], types=types)
    assert plan.warnings == ()


def test_the_long_day_without_time_warns_and_has_no_long_session() -> None:
    avail = availability((90, 90, 90, 90, 90, 90, 0), long_day=6)
    plan = _plan([race("a", _weeks_ahead(12), distance_m=21_097)], avail=avail)
    assert LongDayUnavailable() in plan.warnings
    assert SessionType.LONG not in _types(plan)


def test_long_sessions_fall_on_the_long_day() -> None:
    plan = _plan(
        [race("a", _weeks_ahead(12))], avail=availability(long_day=5, max_week_minutes=900)
    )
    longs = [
        d.day.weekday()
        for d in plan.days
        if d.session and d.session.session_type is SessionType.LONG
    ]
    assert longs
    assert set(longs) == {5}


# --- Starting levels -----------------------------------------------------------------------------
def test_a_plan_from_ctl_zero_starts_gently() -> None:
    plan = _plan([race("a", _weeks_ahead(16))], avail=BIG_WEEK, ctl=0, atl=0)
    first_week = sum(d.load for d in plan.days[:7])
    assert 0 < first_week < 250
    assert plan.projection[6].ctl <= 5.0


def test_a_plan_from_ctl_100_is_possible_and_holds_the_level() -> None:
    plan = _plan([race("a", _weeks_ahead(16), distance_m=5_000)], avail=BIG_WEEK, ctl=100, atl=100)
    assert all(p.ctl > 70 for p in plan.projection)


# --- Fitness goals, injury, horizon --------------------------------------------------------------
def test_fitness_goal_is_pursued_without_a_race() -> None:
    plan = _plan([], [fitness_goal("f", _weeks_ahead(16), 60)], avail=BIG_WEEK, ctl=30, atl=30)
    assert plan.days[-1].day == _weeks_ahead(16)
    assert plan.days[-1].race is None
    assert plan.projection[-1].ctl >= 60 - 5
    assert Phase.TAPER not in _phases(plan)


def test_fitness_goal_on_the_day_of_an_a_race_sets_its_ctl() -> None:
    day = _weeks_ahead(16)
    plan = _plan([race("a", day)], [fitness_goal("f", day, 100)], avail=BIG_WEEK, ctl=30, atl=30)
    warning = next(w for w in plan.warnings if isinstance(w, CtlTargetNotReached))
    assert (warning.goal_id, warning.target_ctl) == ("a", 100)


def test_after_an_injury_the_ramp_is_three_not_five() -> None:
    goal = [race("a", _weeks_ahead(12), distance_m=21_097)]
    hurt = _plan(goal, avail=BIG_WEEK, ctl=10, atl=10, after_injury=True)  # `_plan` checks the cap
    fit = _plan(goal, avail=BIG_WEEK, ctl=10, atl=10)
    assert dict(hurt.parameters)["max_ramp_per_week"] == 3.0
    assert dict(fit.parameters)["max_ramp_per_week"] == 5.0
    assert max(p.ctl for p in hurt.projection) < max(p.ctl for p in fit.projection)


def test_the_plan_never_goes_beyond_52_weeks() -> None:
    far = TODAY + timedelta(days=800)
    plan = _plan([race("a", far)], avail=BIG_WEEK)
    assert len(plan.days) == 364
    assert GoalBeyondHorizon("a") in plan.warnings
    assert {d.phase for d in plan.days} == {Phase.MAINTENANCE}


# --- Where to repeat the threshold test ----------------------------------------------------------
def test_without_a_test_it_is_proposed_before_the_plan() -> None:
    assert _plan([race("a", _weeks_ahead(12))]).retest_proposal == RetestBeforePlan()


def test_the_retest_goes_in_an_unload_week_inside_the_window() -> None:
    window = (TODAY + timedelta(days=14), TODAY + timedelta(days=42))
    base = inputs([race("a", _weeks_ahead(30))], avail=BIG_WEEK)
    plan = build_plan(replace(base, retest=Recommended(*window)), TODAY)
    assert isinstance(plan, Plan)
    proposal = plan.retest_proposal
    assert isinstance(proposal, RetestInUnloadWeek)
    week = next(w for w in plan.weeks if w.week_start == proposal.week_start)
    assert week.kind is WeekKind.UNLOAD
    assert window[0] <= proposal.week_start + timedelta(days=6)
    assert proposal.week_start <= window[1]


def test_an_overdue_retest_takes_the_first_unload_week() -> None:
    base = inputs([race("a", _weeks_ahead(30))], avail=BIG_WEEK)
    plan = build_plan(replace(base, retest=Overdue(3, TODAY - timedelta(days=3))), TODAY)
    assert isinstance(plan, Plan)
    first_unload = next(w.week_start for w in plan.weeks if w.kind is WeekKind.UNLOAD)
    assert plan.retest_proposal == RetestInUnloadWeek(first_unload)


def test_a_window_without_unload_weeks_is_not_placeable() -> None:
    base = inputs([race("a", _weeks_ahead(30))], avail=BIG_WEEK)
    window = NotYet(TODAY + timedelta(days=1), TODAY + timedelta(days=6))  # inside the first week
    plan = build_plan(replace(base, retest=window), TODAY)
    assert isinstance(plan, Plan)
    assert plan.retest_proposal == RetestNotPlaceable()
