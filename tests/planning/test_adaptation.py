# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-04: the plan adapts by being rebuilt from the real PMC; the past stays history."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.goals.domain import SessionType
from ponos_coach.load.domain import compute_pmc
from ponos_coach.planning.domain import (
    Phase,
    Plan,
    RecoveryInserted,
    build_plan,
    compare_with_actual,
)
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race, with_fitness
from tests.planning.invariants import violations

GOAL = [race("a", TODAY + timedelta(weeks=16), distance_m=21_097)]


def _plan(ctl: float, atl: float, **kwargs: object) -> Plan:
    result = build_plan(inputs(GOAL, avail=BIG_WEEK, ctl=ctl, atl=atl, **kwargs), TODAY)  # type: ignore[arg-type]
    assert isinstance(result, Plan)
    assert violations(result) == []
    return result


def _recovery_days(plan: Plan) -> int:
    return sum(1 for d in plan.days if d.phase is Phase.RECOVERY)


# --- Too much fatigue: two days of recovery ------------------------------------------------------
@pytest.mark.parametrize(
    ("ctl", "atl", "recovers"),
    [
        (100.0, 130.0, False),  # TSB exactly -30: not below
        (100.0, 130.5, True),  # TSB -30.5
        (50.0, 100.0, True),
        (40.0, 60.0, False),  # ATL/CTL exactly 1.5 and TSB -20
        (40.0, 60.5, True),  # ratio just above 1.5
        (5.0, 20.0, False),  # ratio 4 but CTL too small for a ratio to mean anything
        (10.0, 15.5, True),  # CTL 10: the ratio counts (1.55)
        (0.0, 0.0, False),
        (100.0, 100.0, False),
    ],
)
def test_recovery_is_inserted_at_the_thresholds(ctl: float, atl: float, recovers: bool) -> None:
    plan = _plan(ctl, atl)
    assert (_recovery_days(plan) == 2) is recovers
    assert any(isinstance(w, RecoveryInserted) for w in plan.warnings) is recovers


def test_the_recovery_days_are_the_first_two_and_hold_a_recovery_session() -> None:
    plan = _plan(50.0, 100.0)
    first, second, third = plan.days[:3]
    assert (first.phase, second.phase) == (Phase.RECOVERY, Phase.RECOVERY)
    assert third.phase is not Phase.RECOVERY
    for day in (first, second):
        assert day.session is not None
        assert day.session.session_type is SessionType.RECOVERY
        assert day.session.minutes <= 30
    warning = next(w for w in plan.warnings if isinstance(w, RecoveryInserted))
    assert warning.days == 2
    assert warning.tsb == pytest.approx(-50.0)


def test_without_the_recovery_type_the_recovery_days_are_rest() -> None:
    types = set(SessionType) - {SessionType.RECOVERY}
    plan = _plan(50.0, 100.0, types=types)
    assert [d.session for d in plan.days[:2]] == [None, None]
    assert [d.load for d in plan.days[:2]] == [0.0, 0.0]


def test_after_the_recovery_the_load_does_not_catch_up() -> None:
    tired = _plan(50.0, 100.0)
    # the first full week after the recovery: no more CTL gained than the ramp allows
    week = tired.days[2:9]
    gain = tired.projection[8].ctl - tired.projection[1].ctl
    assert gain <= 5.0 + 1e-6
    assert sum(d.load for d in week) < 7 * 80  # not a pay-back of the days lost


# --- Skipped sessions, unplanned activities, a changed past --------------------------------------
def test_skipped_sessions_show_as_missed_load() -> None:
    plan = _plan(40.0, 40.0)
    week_ago = TODAY + timedelta(days=7)
    adherence = compare_with_actual(plan, {}, week_ago)
    assert len(adherence) == 7
    assert [a.missed_load for a in adherence] == [d.load for d in plan.days[:7]]
    assert sum(a.missed_load for a in adherence) > 0


def test_extra_work_does_not_cancel_a_skipped_day() -> None:
    plan = _plan(40.0, 40.0)
    day = plan.days[2]
    done = {plan.days[1].day: plan.days[1].load + 90.0, day.day: 0.0}
    by_day = {a.day: a for a in compare_with_actual(plan, done, day.day)}
    assert by_day[plan.days[1].day].missed_load == 0.0
    assert by_day[day.day].missed_load == day.load


def test_only_days_up_to_today_are_compared() -> None:
    plan = _plan(40.0, 40.0)
    assert compare_with_actual(plan, {}, TODAY) == ()
    assert len(compare_with_actual(plan, {}, TODAY + timedelta(days=3))) == 3


def test_a_week_of_skipped_sessions_lowers_the_new_plan_without_catching_up() -> None:
    as_planned = _plan(40.0, 40.0)
    # a week with no activity: CTL and ATL decay, as the real PMC would show
    lost = compute_pmc(
        {TODAY - timedelta(days=60 - d): 45.0 for d in range(0, 53)},
        TODAY - timedelta(days=60),
        TODAY,
    )
    assert lost.ctl[-1] > 0
    ctl, atl = float(lost.ctl[-1]), float(lost.atl[-1])
    replanned = _plan(ctl, atl)
    # whatever was lost is not recovered faster than the ramp (checked on every week above)
    assert replanned.projection[6].ctl - ctl <= 5.0 + 1e-6
    assert as_planned.days[0].day == replanned.days[0].day


def test_unplanned_activities_enter_through_the_pmc() -> None:
    quiet = _plan(40.0, 40.0)
    busy = _plan(55.0, 70.0)  # the PMC after an unplanned hard week
    assert sum(d.load for d in busy.days[:7]) > sum(d.load for d in quiet.days[:7])


def test_an_old_activity_arriving_late_changes_the_past_and_so_the_plan() -> None:
    days = {TODAY - timedelta(days=n): 50.0 for n in range(1, 40)}
    before = compute_pmc(days, TODAY - timedelta(days=60), TODAY)
    days[TODAY - timedelta(days=45)] = 300.0  # synced late, six weeks back
    after = compute_pmc(days, TODAY - timedelta(days=60), TODAY)
    assert after.ctl[-1] != before.ctl[-1]
    base = inputs(GOAL, avail=BIG_WEEK)
    plan_before = build_plan(
        with_fitness(base, float(before.ctl[-1]), float(before.atl[-1])), TODAY
    )
    plan_after = build_plan(with_fitness(base, float(after.ctl[-1]), float(after.atl[-1])), TODAY)
    assert isinstance(plan_before, Plan)
    assert isinstance(plan_after, Plan)
    assert plan_before.to_json() != plan_after.to_json()
    assert plan_before.days[0].day == plan_after.days[0].day == TODAY + timedelta(days=1)
