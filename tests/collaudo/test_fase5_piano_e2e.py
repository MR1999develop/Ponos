# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-01, 03, 04 end to end: synthetic runs as FIT files go through the real stack (decoder,
archive, SQLite, load cache, overview) and the plan is rebuilt, saved, read back and frozen.

Checked from the outside: the plan starts from the PMC the overview shows (recomputed here from
the daily loads with the two time constants), the saved plan is the one that was built, the past
becomes history day by day and never changes, a late activity changes the PMC and the plan but not
the frozen days, a tired athlete gets recovery and no catching-up.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import ThresholdProtocol, ThresholdTestResult
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal, SessionChoices, SessionType
from ponos_coach.goals.use_cases import RemoveGoal
from ponos_coach.planning.domain import (
    CloseARaces,
    MissingLongSession,
    Phase,
    Plan,
    PlanRefusal,
    RecoveryInserted,
    RefusalReason,
    TargetClass,
    TargetTimeUnreachable,
)
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.rebuild_plan import PlanBuilt, PlanRefused
from tests.collaudo.fase4_support import Leg, run_fit, wire
from tests.collaudo.fase5_support import (
    TODAY,
    World,
    clone_world,
    local_start,
    make_world,
    race,
    reopen_world,
)

CTL_DAYS, ATL_DAYS = 42, 7  # the time constants of the PMC, written here on purpose


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A closed profile with six weeks of easy runs every other day."""
    base = tmp_path_factory.mktemp("f5-template") / "p"
    base.mkdir()
    built = make_world(base)
    built.import_history(TODAY - timedelta(days=42), TODAY - timedelta(days=1), every=2, bpm=150)
    built.close()
    return base


@pytest.fixture
def world(template: Path, tmp_path: Path) -> Iterator[World]:
    opened = clone_world(template, tmp_path / "w")
    yield opened
    opened.close()


def pmc_by_hand(world: World) -> tuple[float, float]:
    """CTL and ATL at the end of today from the loads the overview shows, written out again."""
    loads: dict[date, float] = {}
    for activity in world.overview().activities:
        loads[activity.local_day] = loads.get(activity.local_day, 0.0) + activity.load.trimp_eq
    day, ctl, atl = min(loads), 0.0, 0.0
    while day <= world.today:
        load = loads.get(day, 0.0)
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        day += timedelta(days=1)
    return ctl, atl


def week_gains(plan: Plan) -> list[float]:
    """CTL gained in each Monday-Sunday week of the plan, race days left out (rebuilt by hand)."""
    ctl = plan.inputs.fitness.ctl
    gains: list[float] = []
    start_of_week = ctl
    for day in plan.days:
        if day.day.weekday() == 0 and day is not plan.days[0]:
            gains.append(ctl - start_of_week)
            start_of_week = ctl
        load = day.session.load if day.session is not None else 0.0
        ctl += (load - ctl) / CTL_DAYS
    gains.append(ctl - start_of_week)
    return gains


def frozen_json(world: World, first: date, last: date) -> dict[date, tuple[date, str]]:
    return {f.day.day: (f.planned_on, canonical_json(f.day)) for f in world.past(first, last)}


# --- T-PIANO-01 ------------------------------------------------------------------------------
def test_t_piano_01_the_same_goals_in_another_order_give_the_same_saved_bytes(
    template: Path, tmp_path: Path
) -> None:
    goals = [
        race("a1", TODAY + timedelta(days=112), Priority.A, 21_097),
        race("b1", TODAY + timedelta(days=70), Priority.B, 10_000),
        race("c1", TODAY + timedelta(days=35), Priority.C, 5_000),
    ]
    texts = []
    for number, order in enumerate((goals, list(reversed(goals)))):
        opened = clone_world(template, tmp_path / f"w{number}")
        try:
            for goal in order:
                opened.add_goal(goal)
            opened.built()
            texts.append(opened.saved_plan().to_json())
        finally:
            opened.close()
    assert texts[0] == texts[1]
    assert texts[0].isascii()


def test_t_piano_01_the_plan_saved_and_read_after_a_reopen_is_the_plan_that_was_built(
    world: World, tmp_path: Path
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=98), Priority.A, 21_097))
    built = world.built()
    assert built.saved
    text = built.plan.to_json()
    world.close()

    again = reopen_world(world.base, today=TODAY)
    try:
        stored = again.current()
        assert stored is not None
        assert isinstance(stored.state, Plan)
        assert stored.state.to_json() == text
        assert stored.generated_on == TODAY
        second = again.built()
        assert not second.saved  # same inputs: nothing is written
        assert second.plan.to_json() == text
    finally:
        again.close()


# --- The plan starts from the real PMC -----------------------------------------------------------
def test_the_plan_starts_from_the_pmc_of_the_real_activities_recomputed_by_hand(
    world: World,
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=98), Priority.A, 21_097))
    plan = world.built().plan
    ctl, atl = pmc_by_hand(world)
    assert plan.inputs.fitness.ctl == pytest.approx(ctl, abs=1e-6)
    assert plan.inputs.fitness.atl == pytest.approx(atl, abs=1e-6)
    assert ctl > 5  # six weeks of runs: the PMC is not the empty one
    first = plan.projection[0]
    load = plan.days[0].load
    assert first.day == TODAY + timedelta(days=1)
    assert first.ctl == pytest.approx(ctl + (load - ctl) / CTL_DAYS, abs=1e-6)
    assert first.atl == pytest.approx(atl + (load - atl) / ATL_DAYS, abs=1e-6)
    assert first.tsb == pytest.approx(ctl - atl, abs=1e-6)  # the morning form


def test_the_projection_follows_the_planned_loads_day_by_day(world: World) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=120), Priority.A, 42_195))
    plan = world.built().plan
    ctl, atl = plan.inputs.fitness.ctl, plan.inputs.fitness.atl
    assert len(plan.projection) == len(plan.days)
    for day, point in zip(plan.days, plan.projection, strict=True):
        assert point.day == day.day
        assert point.tsb == pytest.approx(ctl - atl, abs=1e-6)
        ctl += (day.load - ctl) / CTL_DAYS
        atl += (day.load - atl) / ATL_DAYS
        assert point.ctl == pytest.approx(ctl, abs=1e-6)
        assert point.atl == pytest.approx(atl, abs=1e-6)
        expected = (day.session.load if day.session else 0.0) + (day.race.load if day.race else 0.0)
        assert day.load == pytest.approx(expected, abs=1e-9)


def test_the_saved_plan_stays_inside_the_ramp_week_by_week(world: World) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=140), Priority.A, 42_195))
    plan = world.built().plan
    gains = week_gains(plan)
    assert max(gains) <= 5.0 + 1e-6
    assert max(gains) > 1.0  # the plan does ramp up: the bound is not met by doing nothing


# --- T-PIANO-03 end to end ------------------------------------------------------------------------
def test_t_piano_03_two_a_races_three_weeks_apart_warn_and_are_saved(world: World) -> None:
    first = TODAY + timedelta(days=84)
    world.add_goal(race("a1", first, Priority.A, 21_097))
    world.add_goal(race("a2", first + timedelta(days=21), Priority.A, 21_097))
    plan = world.built().plan
    assert any(isinstance(w, CloseARaces) for w in plan.warnings)
    assert world.saved_plan().to_json() == plan.to_json()
    between = [d for d in plan.days if first < d.day < first + timedelta(days=21)]
    assert all(d.phase in {Phase.RECOVERY, Phase.MAINTENANCE, Phase.TAPER} for d in between)


def test_t_piano_03_a_goal_that_has_become_past_is_a_saved_refusal_and_the_old_plan_is_history(
    world: World,
) -> None:
    world.add_goal(race("soon", TODAY + timedelta(days=1), Priority.A, 5_000))
    planned = world.built().plan
    race_day = planned.days[-1]
    assert race_day.race is not None
    world.today = TODAY + timedelta(days=2)  # the race is behind us

    result = world.rebuild(world.today)

    assert isinstance(result, PlanRefused)
    assert result.refusal == PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("soon",))
    assert result.saved
    stored = world.current()
    assert stored is not None
    assert stored.state == result.refusal
    assert stored.generated_on == world.today
    frozen = world.past(TODAY, world.today)
    assert [f.day.day for f in frozen] == [TODAY + timedelta(days=1)]
    assert frozen[0].day.race is not None
    assert frozen[0].day.race.goal_id == "soon"
    assert frozen[0].planned_on == TODAY


def test_t_piano_03_no_availability_is_a_refusal_and_a_goal_today_is_too(world: World) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=60), Priority.A))
    world.set_availability(None)
    refused = world.rebuild(TODAY)
    assert isinstance(refused, PlanRefused)
    assert refused.refusal.reason is RefusalReason.NO_AVAILABILITY
    world.add_goal(race("today", TODAY, Priority.B, 5_000))
    refused = world.rebuild(TODAY)
    assert isinstance(refused, PlanRefused)
    assert refused.refusal.reason is RefusalReason.GOAL_NOT_IN_FUTURE
    assert refused.refusal.goal_ids == ("today",)


# --- T-PIANO-04: adaptation after a synchronisation ----------------------------------------------
def test_t_piano_04_skipped_sessions_are_missed_load_and_the_new_plan_does_not_catch_up(
    world: World,
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=112), Priority.A, 21_097))
    first = world.built().plan
    ctl_before = first.inputs.fitness.ctl
    world.today = TODAY + timedelta(days=14)  # two weeks without a single activity

    result = world.rebuild(world.today)

    assert isinstance(result, PlanBuilt)
    second = result.plan
    assert second.inputs.fitness.ctl < ctl_before  # what was lost stays lost
    assert second.days[0].day == world.today + timedelta(days=1)
    assert max(week_gains(second)) <= 5.0 + 1e-6  # ...and is not recovered faster than the ramp
    lost = world.adherence(TODAY + timedelta(days=1), world.today)
    assert len(lost) == 14
    planned_loads = [d.load for d in first.days[:14]]
    assert [a.planned_load for a in lost] == pytest.approx(planned_loads, abs=1e-3)
    assert all(a.actual_load == 0.0 for a in lost)
    assert [a.missed_load for a in lost] == pytest.approx(planned_loads, abs=1e-3)
    assert sum(planned_loads) > 0
    # the first two weeks of the new plan do not carry the lost load on top of their own
    assert (
        sum(d.load for d in second.days[:14])
        <= sum(d.load for d in first.days[14:28]) * 1.25 + 1e-6
    )


def test_t_piano_04_a_hard_week_gives_recovery_days_not_a_catch_up(world: World) -> None:
    for back in range(7, 0, -1):
        world.import_run(TODAY - timedelta(days=back), minutes=120, bpm=175, speed=3.5)
    world.add_goal(race("a", TODAY + timedelta(days=100), Priority.A, 21_097))
    plan = world.built().plan

    fitness = plan.inputs.fitness
    assert fitness.ctl - fitness.atl < -30  # the setup really is tired (the real PMC says so)
    assert [d.phase for d in plan.days[:2]] == [Phase.RECOVERY, Phase.RECOVERY]
    assert all(
        d.session is None or d.session.session_type.value == "recovery" for d in plan.days[:2]
    )
    assert any(isinstance(w, RecoveryInserted) for w in plan.warnings)
    assert plan.days[2].phase is not Phase.RECOVERY
    assert max(week_gains(plan)) <= 5.0 + 1e-6


def test_t_piano_04_an_unplanned_activity_enters_the_pmc_and_the_next_plan(world: World) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=100), Priority.A, 21_097))
    before = world.built().plan
    world.import_run(TODAY, minutes=90, bpm=172, hour=18)  # nobody planned this one

    after = world.built().plan

    assert after.inputs.fitness.ctl > before.inputs.fitness.ctl
    assert after.inputs.fitness.atl > before.inputs.fitness.atl
    assert after.to_json() != before.to_json()
    assert world.saved_plan().to_json() == after.to_json()
    ctl, atl = pmc_by_hand(world)
    assert after.inputs.fitness.ctl == pytest.approx(ctl, abs=1e-6)
    assert after.inputs.fitness.atl == pytest.approx(atl, abs=1e-6)


def test_t_piano_04_an_old_activity_that_arrives_late_changes_the_plan_not_the_frozen_days(
    world: World,
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=100), Priority.A, 21_097))
    world.built()
    world.today = TODAY + timedelta(days=5)
    world.built()
    frozen_before = frozen_json(world, TODAY, world.today)
    assert len(frozen_before) == 5
    plan_before = world.saved_plan().to_json()
    ctl_before = world.saved_plan().inputs.fitness.ctl

    world.import_run(TODAY - timedelta(days=31), minutes=150, bpm=176, speed=3.6)  # six weeks late
    world.today = TODAY + timedelta(days=6)
    result = world.built()

    assert result.plan.inputs.fitness.ctl != pytest.approx(ctl_before, abs=1e-3)
    assert result.plan.to_json() != plan_before
    after = frozen_json(world, TODAY, world.today)
    assert {d: v for d, v in after.items() if d in frozen_before} == frozen_before
    assert len(after) == 6


# --- The past stays history -----------------------------------------------------------------------
def test_the_past_is_frozen_day_by_day_from_the_plan_that_held_it_and_never_changes(
    world: World,
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=120), Priority.A, 21_097))
    snapshots: dict[date, Plan] = {}
    seen: dict[date, tuple[date, str]] = {}
    steps = [1, 1, 1, 5, 1, 2, 1, 1]  # a jump of five days: five days frozen in one rebuild
    for index, step in enumerate(steps):
        built = world.built()
        snapshots[world.today] = built.plan  # the last plan generated on that date
        world.today += timedelta(days=step)
        if index % 2 == 0:
            world.import_run(world.today - timedelta(days=1), minutes=40 + index, bpm=148 + index)
        world.built()
        snapshots[world.today] = world.saved_plan()
        now = frozen_json(world, TODAY, world.today)
        for day, value in seen.items():
            assert now[day] == value, f"{day} changed after it was frozen"
        seen = now
        for day, (planned_on, text) in now.items():
            origin = snapshots[planned_on]
            held = next(d for d in origin.days if d.day == day)
            assert planned_on < day
            assert text == canonical_json(held)
    assert sorted(seen) == [TODAY + timedelta(days=n) for n in range(1, sum(steps) + 1)]


def test_the_database_itself_refuses_to_change_a_frozen_day(world: World) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=90), Priority.A))
    world.built()
    world.today = TODAY + timedelta(days=3)
    world.built()
    assert len(world.past(TODAY, world.today)) == 3
    world.close()
    path = world.base / "profilo" / PROFILE_DATABASE_NAME
    with closing(sqlite3.connect(path)) as connection:
        for statement in (
            "UPDATE plan_past_day SET day_json = '{}'",
            "UPDATE plan_past_day SET planned_on = '1999-01-01'",
            "DELETE FROM plan_past_day",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="history"):
                connection.execute(statement)


def test_a_same_day_change_of_data_replaces_the_plan_without_freezing_anything(
    world: World,
) -> None:
    world.add_goal(race("a", TODAY + timedelta(days=90), Priority.A))
    world.built()
    world.import_run(TODAY, minutes=70, bpm=165, hour=19)
    result = world.built()
    assert result.saved
    assert world.past(TODAY, TODAY + timedelta(days=30)) == ()


# --- More of T-PIANO-03 and T-PIANO-05 on the real stack ------------------------------------------
def test_t_piano_03_a_new_profile_with_no_activity_starts_from_ctl_zero_and_still_gets_a_plan(
    tmp_path: Path,
) -> None:
    empty = make_world(tmp_path / "empty")
    try:
        empty.add_goal(race("a", TODAY + timedelta(days=84), Priority.A, 10_000))

        plan = empty.built().plan

        assert (plan.inputs.fitness.ctl, plan.inputs.fitness.atl) == (0.0, 0.0)
        assert plan.days[0].day == TODAY + timedelta(days=1)
        assert max(week_gains(plan)) <= 5.0 + 1e-6
        assert sum(1 for d in plan.days if d.session is not None) > 20  # the frequency is there
    finally:
        empty.close()


def test_t_piano_03_a_marathon_without_the_long_session_warns_and_never_adds_it(
    world: World,
) -> None:
    world.set_choices(SessionChoices.of({Sport.RUNNING: set(SessionType) - {SessionType.LONG}}))
    world.add_goal(race("m", TODAY + timedelta(days=140), Priority.A, 42_195))

    plan = world.built().plan

    assert any(isinstance(w, MissingLongSession) and w.goal_id == "m" for w in plan.warnings)
    assert all(
        d.session is None or d.session.session_type is not SessionType.LONG for d in plan.days
    )
    assert world.saved_plan().to_json() == plan.to_json()


def test_t_piano_05_the_target_time_is_classified_from_the_pace_of_the_test_by_hand(
    world: World,
) -> None:
    lab = wire(world.profile, TODAY)
    data = run_fit([Leg(1200, 160, 3.5)], local_start(TODAY - timedelta(days=5), 8), serial=4242)
    result = lab.evaluate(lab.import_bytes(data).identity, ThresholdProtocol.TWENTY_MINUTES)
    assert isinstance(result, ThresholdTestResult)
    assert result.pace_s_per_km == 300
    lab.apply(result)
    # 10 km at 300 s/km of threshold: 12 000 m in an hour, Riegel 3600 x (10/12)^1.06 = 2967.3 s;
    # in 70 days the improvement is 10 weeks x 0.5% = 5%: the best time is 2818.9 s
    day = TODAY + timedelta(days=70)
    expected = {
        2_900: TargetClass.REALISTIC,
        2_780: TargetClass.AMBITIOUS,
        2_600: TargetClass.UNREACHABLE,
    }
    for target, classification in expected.items():
        world.add_goal(RaceGoal(GoalId("ten"), day, Priority.A, Sport.RUNNING, 10_000, target))
        plan = world.built().plan
        [assessment] = plan.targets
        assert assessment.classification is classification, target
        assert assessment.best_time_s == pytest.approx(2819, abs=2)
        assert any(isinstance(w, TargetTimeUnreachable) for w in plan.warnings) is (
            classification is TargetClass.UNREACHABLE
        )
        RemoveGoal(world.profile.session.activities, world.profile.session.goals)(GoalId("ten"))
