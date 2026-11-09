# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-07 and T-NUT-08 on the real stack: the nutrition of the days ahead, from the plan that is
saved in the profile database.

The athlete loses weight (500 g a week). The plan has an A race, a B race and a C race. For every
day of the plan the guidance is read through `PlanDayNutrition` and compared with a rule written
again here: no deficit in the Monday-to-Sunday week of an A or B race and in the days of taper that
precede it; deficit again outside; nothing for a C race. Then the same on random plans (hypothesis).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal
from ponos_coach.nutrition.domain import (
    Advisory,
    EnergySource,
    GoalPeriod,
    MaintenanceGoal,
    MealSlot,
    PerformanceGoal,
    WeightLossGoal,
    defaults,
)
from ponos_coach.planning.domain import Phase, Plan, PlanRefusal, RefusalReason, build_plan
from ponos_coach.profiles.day_nutrition import (
    DEFAULT_TRAINING_MINUTE,
    DayNutritionReport,
    NutritionUnavailable,
    UnavailableReason,
)
from tests.collaudo.fase5_support import (
    FIRST_DAY,
    ROME,
    TODAY,
    World,
    clone_world,
    make_world,
    race,
)
from tests.collaudo.support import create_profile
from tests.planning.builders import inputs

A_DAY = TODAY + timedelta(days=100)  # Wednesday 10 June
B_DAY = TODAY + timedelta(days=60)  # Friday 1 May
C_DAY = TODAY + timedelta(days=30)  # Wednesday 1 April
LATER_C = A_DAY + timedelta(days=14)  # a C race that makes the plan go on after the A race


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("f5-nut") / "p"
    base.mkdir()
    built = make_world(base)
    built.import_history(TODAY - timedelta(days=42), TODAY - timedelta(days=1), every=3)
    built.close()
    return base


def new_world(template: Path, tmp_path: Path, goal: object, races: list[RaceGoal]) -> World:
    opened = clone_world(template, tmp_path / "w")
    store = opened.profile.session.goal_periods
    store.add(GoalPeriod(FIRST_DAY, None, goal))  # type: ignore[arg-type]
    for goal_race in races:
        opened.add_goal(goal_race)
    opened.built()
    return opened


RACES = [
    race("c1", C_DAY, Priority.C, 5_000),
    race("b1", B_DAY, Priority.B, 10_000),
    race("a1", A_DAY, Priority.A, 21_097),
    race("c2", LATER_C, Priority.C, 5_000),
]


@pytest.fixture
def loss(template: Path, tmp_path: Path) -> Iterator[World]:
    opened = new_world(template, tmp_path, WeightLossGoal(500), RACES)
    yield opened
    opened.close()


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def paused_by_rule(plan: Plan, day: date) -> bool:
    """The rule of ADR 011, from the goals (the week) and the plan's phases (the taper)."""
    week_of_race = any(
        r.priority in (Priority.A, Priority.B) and monday(r.day) == monday(day)
        for r in plan.inputs.races
    )
    phase = next(d.phase for d in plan.days if d.day == day)
    return week_of_race or phase in (Phase.TAPER, Phase.MINI_TAPER)


# --- Every day of the plan ------------------------------------------------------------------------
def test_t_nut_08_no_deficit_in_the_race_week_and_the_taper_and_a_deficit_everywhere_else(
    loss: World,
) -> None:
    plan = loss.saved_plan()
    paused_days = 0
    free_days = 0
    for planned in plan.days:
        report = loss.report(planned.day)
        n = report.nutrition
        assert report.from_plan
        assert isinstance(report.goal, WeightLossGoal)  # the goal shown is the one the user chose
        if paused_by_rule(plan, planned.day):
            paused_days += 1
            assert n.deficit_kcal == 0, planned.day
            assert n.target_kcal == n.expenditure_kcal, planned.day
            assert Advisory.DEFICIT_PAUSED_FOR_RACE in n.advisories, planned.day
        else:
            free_days += 1
            assert Advisory.DEFICIT_PAUSED_FOR_RACE not in n.advisories, planned.day
            assert 0 <= n.deficit_kcal <= 750
    assert paused_days > 20 and free_days > 30  # both sides of the rule are really exercised


def test_t_nut_08_a_paused_day_is_exactly_the_maintenance_day(
    template: Path, tmp_path: Path, loss: World
) -> None:
    keep = new_world(template, tmp_path / "keep", MaintenanceGoal(), RACES)
    try:
        plan = loss.saved_plan()
        paused = [d.day for d in plan.days if paused_by_rule(plan, d.day)]
        assert paused
        for day in paused:
            mine, theirs = loss.report(day).nutrition, keep.report(day).nutrition
            assert (mine.target_kcal, mine.macros, mine.meals) == (
                theirs.target_kcal, theirs.macros, theirs.meals,
            ), day  # fmt: skip
            assert set(mine.advisories) == {*theirs.advisories, Advisory.DEFICIT_PAUSED_FOR_RACE}
    finally:
        keep.close()


def test_t_nut_08_the_taper_of_the_a_race_and_the_mini_taper_of_the_b_race_are_paused(
    loss: World,
) -> None:
    plan = loss.saved_plan()
    taper = [d.day for d in plan.days if d.phase is Phase.TAPER]
    mini = [d.day for d in plan.days if d.phase is Phase.MINI_TAPER]
    assert len(taper) == 14 and len(mini) == 4  # half marathon, B race
    assert taper[-1] == A_DAY - timedelta(days=1) and mini[-1] == B_DAY - timedelta(days=1)
    for day in (*taper, *mini):
        assert loss.report(day).nutrition.deficit_kcal == 0


def test_t_nut_08_the_week_of_the_a_race_is_paused_also_after_the_race_and_the_next_week_is_not(
    loss: World,
) -> None:
    after_race = [A_DAY + timedelta(days=n) for n in range(1, 5)]  # Thursday to Sunday
    assert after_race[-1].weekday() == 6
    for day in after_race:
        assert loss.report(day).nutrition.deficit_kcal == 0, day
        assert Advisory.DEFICIT_PAUSED_FOR_RACE in loss.report(day).nutrition.advisories
    next_monday = after_race[-1] + timedelta(days=1)
    assert Advisory.DEFICIT_PAUSED_FOR_RACE not in loss.report(next_monday).nutrition.advisories
    assert loss.report(next_monday).nutrition.deficit_kcal > 0


def test_t_nut_08_a_c_race_pauses_nothing_not_even_its_own_day(loss: World) -> None:
    for day in (C_DAY - timedelta(days=3), C_DAY, C_DAY + timedelta(days=1)):
        n = loss.report(day).nutrition
        assert Advisory.DEFICIT_PAUSED_FOR_RACE not in n.advisories, day
        assert n.deficit_kcal > 0, day


@pytest.mark.parametrize(
    "goal", [MaintenanceGoal(), PerformanceGoal()], ids=["maintenance", "performance"]
)
def test_t_nut_08_without_a_weight_loss_goal_there_is_no_pause_and_no_advisory(
    template: Path, tmp_path: Path, goal: object
) -> None:
    opened = new_world(template, tmp_path, goal, RACES)
    try:
        plan = opened.saved_plan()
        for planned in plan.days:
            n = opened.report(planned.day).nutrition
            assert Advisory.DEFICIT_PAUSED_FOR_RACE not in n.advisories
            assert n.deficit_kcal == 0
    finally:
        opened.close()


def test_t_nut_08_a_weight_loss_period_that_starts_in_the_race_week_is_paused_from_its_first_day(
    template: Path, tmp_path: Path
) -> None:
    opened = clone_world(template, tmp_path / "late")
    try:
        start = A_DAY - timedelta(days=2)
        opened.profile.session.goal_periods.add(GoalPeriod(start, None, WeightLossGoal(500)))
        for goal_race in RACES:
            opened.add_goal(goal_race)
        opened.built()

        assert opened.report(start).nutrition.deficit_kcal == 0
        assert opened.report(A_DAY + timedelta(days=8)).nutrition.deficit_kcal > 0  # Monday after
    finally:
        opened.close()


# --- What a planned day is ------------------------------------------------------------------------
def test_t_nut_07_a_planned_day_has_the_session_at_the_typical_time_with_the_energy_of_its_met(
    loss: World,
) -> None:
    plan = loss.saved_plan()
    starts: dict[int, list[int]] = {}
    for record in loss.profile.records():
        local = record.start.astimezone(ROME)
        if TODAY - timedelta(days=60) <= local.date() <= TODAY:
            starts.setdefault(local.weekday(), []).append(local.hour * 60 + local.minute)
    typical = {w: round(sum(m) / len(m)) for w, m in starts.items()}
    checked = 0
    for planned in plan.days:
        if planned.session is None or checked >= 25:
            continue
        report = loss.report(planned.day)
        (activity,) = report.activities
        assert activity.identity is None
        assert activity.duration_s == planned.session.minutes * 60
        assert activity.load == pytest.approx(planned.session.load)
        assert activity.energy.source is EnergySource.MET
        weight = 80.0
        met = defaults.SPORT_MET[planned.session.sport]
        assert activity.energy.total_kcal == pytest.approx(
            met * weight * planned.session.minutes / 60
        )
        assert activity.local_start_minute == typical.get(
            planned.day.weekday(), DEFAULT_TRAINING_MINUTE
        )
        slots = {slot for item in report.nutrition.meals for slot in item.slots}
        if planned.session.minutes > 60:
            assert MealSlot.PRE_TRAINING in slots
        checked += 1
    assert checked == 25


def test_t_nut_07_the_race_is_a_session_of_its_day_with_the_time_the_plan_expects(
    loss: World,
) -> None:
    plan = loss.saved_plan()
    race_day = next(d for d in plan.days if d.race is not None and d.race.goal_id == "a1")
    assert race_day.race is not None

    report = loss.report(race_day.day)

    (activity,) = report.activities
    assert activity.sport is Sport.RUNNING
    assert activity.duration_s == race_day.race.expected_time_s
    assert activity.load == pytest.approx(race_day.race.load)
    assert report.nutrition.macros.carbs_g > 0
    slots = {slot for item in report.nutrition.meals for slot in item.slots}
    assert {MealSlot.PRE_TRAINING, MealSlot.POST_TRAINING} <= slots


def test_t_nut_07_the_next_planned_session_is_a_day_away_so_the_refuelling_is_a_single_dose(
    loss: World,
) -> None:
    plan = loss.saved_plan()
    for planned in plan.days[:30]:
        if planned.session is None:
            continue
        items = loss.report(planned.day).nutrition.meals
        assert all(item.refuel_intake is None for item in items), planned.day


def test_t_nut_07_the_same_data_gives_byte_for_byte_the_same_guidance(loss: World) -> None:
    plan = loss.saved_plan()
    for planned in plan.days[:40]:
        assert loss.report(planned.day) == loss.report(planned.day)


def test_t_nut_07_a_change_of_the_plan_changes_the_guidance_of_the_days_ahead_only(
    loss: World,
) -> None:
    day = TODAY + timedelta(days=20)
    before = loss.report(day)
    loss.import_run(TODAY, minutes=90, bpm=172, hour=18)  # the athlete trained hard today
    loss.built()
    after = loss.report(day)

    assert after.from_plan and before.from_plan
    assert after != before  # the plan moved with the new PMC
    past = loss.report(TODAY - timedelta(days=2))
    assert not past.from_plan


# --- No guidance ----------------------------------------------------------------------------------
def test_t_nut_07_without_a_plan_nothing_for_the_days_ahead_with_the_reason(
    template: Path, tmp_path: Path
) -> None:
    opened = clone_world(template, tmp_path / "noplan")
    try:
        assert opened.nutrition(TODAY + timedelta(days=1)) == NutritionUnavailable(
            UnavailableReason.NO_PLAN_FOR_DAY
        )
        past = opened.nutrition(TODAY - timedelta(days=1))
        assert isinstance(past, DayNutritionReport) and not past.from_plan
    finally:
        opened.close()


def test_t_nut_07_a_refusal_the_horizon_and_the_day_after_the_last_race_give_no_guidance(
    template: Path, tmp_path: Path
) -> None:
    opened = clone_world(template, tmp_path / "refusal")
    try:
        opened.add_goal(race("today", TODAY, Priority.A, 5_000))
        opened.rebuild(TODAY)
        stored = opened.current()
        assert stored is not None and isinstance(stored.state, PlanRefusal)
        assert stored.state.reason is RefusalReason.GOAL_NOT_IN_FUTURE
        for ahead in (1, 7, 400):
            assert opened.nutrition(TODAY + timedelta(days=ahead)) == NutritionUnavailable(
                UnavailableReason.NO_PLAN_FOR_DAY
            )
    finally:
        opened.close()


def test_t_nut_07_the_day_after_the_plan_ends_and_the_horizon_have_no_guidance(loss: World) -> None:
    plan = loss.saved_plan()
    last = plan.days[-1].day
    assert loss.report(last).from_plan
    assert loss.nutrition(last + timedelta(days=1)) == NutritionUnavailable(
        UnavailableReason.NO_PLAN_FOR_DAY
    )
    assert loss.nutrition(TODAY + timedelta(days=364)) == NutritionUnavailable(
        UnavailableReason.NO_PLAN_FOR_DAY
    )


def test_t_nut_07_the_age_and_body_checks_come_before_the_plan(tmp_path: Path) -> None:
    bare = World(create_profile(tmp_path / "bare"), tmp_path / "bare", seed=False)
    try:
        assert bare.nutrition(TODAY + timedelta(days=3)) == NutritionUnavailable(
            UnavailableReason.MISSING_BODY_DATA
        )
    finally:
        bare.close()


# --- Documented against required ------------------------------------------------------------------
def test_t_nut_08_today_inside_the_race_week_has_no_deficit_either(
    template: Path, tmp_path: Path
) -> None:
    opened = clone_world(template, tmp_path / "week")
    try:
        opened.profile.session.goal_periods.add(GoalPeriod(FIRST_DAY, None, WeightLossGoal(500)))
        opened.add_goal(race("a", TODAY + timedelta(days=2), Priority.A, 5_000))  # Wednesday
        opened.built()
        assert (
            Advisory.DEFICIT_PAUSED_FOR_RACE
            in opened.report(TODAY + timedelta(days=1)).nutrition.advisories
        )
        assert Advisory.DEFICIT_PAUSED_FOR_RACE in opened.report(TODAY).nutrition.advisories
    finally:
        opened.close()


# --- The rule on random plans ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def rule_world(tmp_path_factory: pytest.TempPathFactory, template: Path) -> Iterator[World]:
    opened = clone_world(template, tmp_path_factory.mktemp("f5-rule") / "w")
    opened.profile.session.goal_periods.add(GoalPeriod(FIRST_DAY, None, WeightLossGoal(500)))
    yield opened
    opened.close()


RACE_LISTS = st.lists(
    st.tuples(
        st.integers(1, 200),
        st.sampled_from(list(Priority)),
        st.sampled_from([5_000, 21_097, 42_195]),
    ),
    min_size=1,
    max_size=4,
    unique_by=lambda t: t[0],
)


def check_rule(rule_world: World, races: list[tuple[int, Priority, int]]) -> None:
    goals = tuple(
        RaceGoal(
            GoalId(f"r{i}"), TODAY + timedelta(days=offset), priority, Sport.RUNNING, distance, None
        )
        for i, (offset, priority, distance) in enumerate(races)
    )
    plan = build_plan(replace(inputs(), races=goals), TODAY)
    assert isinstance(plan, Plan)
    rule_world.profile.session.plans.replace_current(plan, TODAY)
    # the days around every race are where the rule changes; the rest is sampled
    near = [
        d.day for d in plan.days
        if any(abs((d.day - g.day).days) <= 9 for g in goals) or (d.day - TODAY).days % 23 == 0
    ]  # fmt: skip
    for day in near:
        n = rule_world.report(day).nutrition
        if paused_by_rule(plan, day):
            assert n.deficit_kcal == 0, day
            assert Advisory.DEFICIT_PAUSED_FOR_RACE in n.advisories, day
        else:
            assert Advisory.DEFICIT_PAUSED_FOR_RACE not in n.advisories, day
    assert rule_world.nutrition(plan.days[-1].day + timedelta(days=1)) == NutritionUnavailable(
        UnavailableReason.NO_PLAN_FOR_DAY
    )


_SETTINGS = {
    "deadline": None,
    "suppress_health_check": [HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
}


@given(RACE_LISTS)
@settings(max_examples=20, **_SETTINGS)  # type: ignore[arg-type]
def test_t_nut_08_the_pause_follows_the_rule_on_any_plan(
    rule_world: World, races: list[tuple[int, Priority, int]]
) -> None:
    check_rule(rule_world, races)


@pytest.mark.collaudo
@given(RACE_LISTS)
@settings(max_examples=300, **_SETTINGS)  # type: ignore[arg-type]
def test_t_nut_08_the_pause_follows_the_rule_on_any_plan_long(
    rule_world: World, races: list[tuple[int, Priority, int]]
) -> None:
    check_rule(rule_world, races)
