# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`PlanDayNutrition` for days after today: the plan's sessions, no deficit around A and B races
(T-NUT-07, T-NUT-08). Made-up athlete; today is Monday 2 March 2026 (the plan's `TODAY`)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import Priority
from ponos_coach.nutrition.domain import (
    Advisory,
    EnergySource,
    GoalPeriod,
    MaintenanceGoal,
    MealSlot,
    WeightLossGoal,
    defaults,
)
from ponos_coach.planning.domain import Phase, Plan, PlanDay, PlanRefusal, RefusalReason, build_plan
from ponos_coach.profiles.day_nutrition import (
    DEFAULT_TRAINING_MINUTE,
    DayNutritionReport,
    DayNutritionResult,
    NutritionSources,
    NutritionUnavailable,
    PlanContext,
    PlanDayNutrition,
    UnavailableReason,
)
from tests.builders import fit_identity, make_record
from tests.planning.builders import BIG_WEEK, TODAY, availability, inputs, race
from tests.profiles.test_day_nutrition import FOODS, ROME, Rig, decode_fit

LOSS = WeightLossGoal(500)
FAR = date(2026, 3, 3)  # day after today


def planned(*races: tuple[str, int, Priority]) -> Plan:
    """The plan the real engine makes for races given as (id, days after today, priority)."""
    goals = [race(i, TODAY + timedelta(days=d), p) for i, d, p in races]
    result = build_plan(inputs(goals, avail=BIG_WEEK), TODAY)
    assert isinstance(result, Plan)
    return result


def rig_with(plan: Plan | PlanRefusal | None, *, loss: bool = True) -> Rig:
    rig = Rig()
    if plan is not None:
        rig.profile.plans.replace_current(plan, TODAY)
    if loss:
        rig.profile.goal_periods.add(GoalPeriod(date(2026, 1, 1), None, LOSS))
    return rig


def result_of(rig: Rig, day: date) -> DayNutritionResult:
    p = rig.profile
    sources = NutritionSources(
        p.activities, rig.archive, p.load_cache, p.physiology, p.sport_weights,
        p.weights, p.fixed_data, p.activity_levels, p.meal_times, p.goal_periods,
        p.lean_masses,
    )  # fmt: skip
    context = PlanContext(p.plans, TODAY)
    return PlanDayNutrition(sources, decode_fit, ROME, FOODS, plan_context=context)(day)


def report_of(rig: Rig, day: date) -> DayNutritionReport:
    result = result_of(rig, day)
    assert isinstance(result, DayNutritionReport)
    return result


def entry(plan: Plan, day: date) -> PlanDay:
    return next(d for d in plan.days if d.day == day)


def session_day(plan: Plan, *, weekday: int | None = None, min_minutes: int = 1) -> PlanDay:
    return next(
        d
        for d in plan.days
        if d.session is not None
        and d.session.minutes >= min_minutes
        and (weekday is None or d.day.weekday() == weekday)
    )


# --- The plan's sessions become the day's activities (T-NUT-07) ---------------------------


def test_a_day_ahead_has_the_planned_session_at_the_default_time_with_the_energy_of_its_met() -> (
    None
):
    plan = planned(("a", 98, Priority.A))
    rig = rig_with(plan, loss=False)
    target = session_day(plan, min_minutes=90)
    assert target.session is not None

    report = report_of(rig, target.day)

    assert report.from_plan
    (activity,) = report.activities
    assert activity.identity is None
    assert activity.sport is target.session.sport
    assert activity.local_start_minute == DEFAULT_TRAINING_MINUTE
    assert activity.duration_s == target.session.minutes * 60
    assert activity.load == target.session.load
    assert activity.energy.source is EnergySource.MET
    expected = defaults.SPORT_MET[target.session.sport] * 80 * target.session.minutes / 60
    assert activity.energy.total_kcal == pytest.approx(expected)
    start = datetime.combine(target.day, datetime.min.time(), tzinfo=ROME) + timedelta(hours=18)
    assert activity.start == start.astimezone(UTC)
    slots = [slot for m in report.meals for slot in m.meal.slots]
    assert MealSlot.PRE_TRAINING in slots
    assert MealSlot.POST_TRAINING in slots


def test_the_typical_time_is_the_mean_start_of_the_last_sixty_days_on_that_weekday() -> None:
    plan = planned(("a", 98, Priority.A))
    rig = rig_with(plan, loss=False)
    # two Mondays before today, local (UTC+1 in winter) 07:00 and 08:00: the mean is 07:30
    for number, (day, hour) in enumerate([(date(2026, 2, 16), 7), (date(2026, 2, 23), 8)]):
        start = datetime(day.year, day.month, day.day, hour - 1, tzinfo=UTC)
        record = make_record(fit_identity(number + 1), start=start)
        rig.profile.activities.add(record)
    monday = session_day(plan, weekday=0)

    report = report_of(rig, monday.day)

    assert report.activities[0].local_start_minute == 7 * 60 + 30


def test_a_rest_day_ahead_has_no_activities_but_still_comes_from_the_plan() -> None:
    no_tuesdays = availability((120, 0, 120, 120, 120, 120, 180), 6, 1200)
    result = build_plan(inputs([race("a", TODAY + timedelta(days=98))], avail=no_tuesdays), TODAY)
    assert isinstance(result, Plan)
    rest = next(d for d in result.days if d.day.weekday() == 1 and d.race is None)

    report = report_of(rig_with(result), rest.day)

    assert report.from_plan
    assert report.activities == ()


def test_the_race_is_a_session_of_its_day() -> None:
    plan = planned(("a", 14, Priority.A))
    race_day = TODAY + timedelta(days=14)

    report = report_of(rig_with(plan, loss=False), race_day)

    (activity,) = report.activities
    assert activity.sport is Sport.RUNNING
    assert activity.duration_s == entry(plan, race_day).race.expected_time_s  # type: ignore[union-attr]


def test_same_data_gives_the_same_guidance() -> None:
    plan = planned(("a", 98, Priority.A))
    rig = rig_with(plan)
    day = session_day(plan).day

    assert result_of(rig, day) == result_of(rig, day)


def test_a_day_up_to_today_is_not_an_estimate_from_the_plan() -> None:
    rig = rig_with(planned(("a", 98, Priority.A)))

    assert not report_of(rig, TODAY).from_plan


# --- No plan, no guidance for a day ahead (T-NUT-07) --------------------------------------


@pytest.mark.parametrize(
    "stored",
    [None, PlanRefusal(RefusalReason.NO_GOALS)],
    ids=["never_built", "refused"],
)
def test_without_a_plan_a_day_ahead_has_no_guidance(stored: PlanRefusal | None) -> None:
    result = result_of(rig_with(stored), FAR)

    assert result == NutritionUnavailable(UnavailableReason.NO_PLAN_FOR_DAY)


def test_a_day_beyond_the_end_of_the_plan_has_no_guidance() -> None:
    plan = planned(("a", 28, Priority.A))
    after_the_plan = plan.days[-1].day + timedelta(days=1)

    result = result_of(rig_with(plan), after_the_plan)

    assert result == NutritionUnavailable(UnavailableReason.NO_PLAN_FOR_DAY)


def test_a_missing_weight_is_reported_before_the_missing_plan() -> None:
    result = result_of(Rig(weight=False), FAR)

    assert result == NutritionUnavailable(UnavailableReason.MISSING_WEIGHT)


# --- No deficit around A and B races (T-NUT-08) -------------------------------------------


def test_far_from_any_race_the_weight_loss_deficit_applies() -> None:
    plan = planned(("a", 98, Priority.A))
    day = TODAY + timedelta(days=10)

    nutrition = report_of(rig_with(plan), day).nutrition

    assert nutrition.deficit_kcal > 0
    assert Advisory.DEFICIT_PAUSED_FOR_RACE not in nutrition.advisories


def test_in_the_taper_before_an_a_race_there_is_no_deficit_and_the_advisory_says_so() -> None:
    plan = planned(("a", 98, Priority.A))
    taper = [d for d in plan.days if d.phase is Phase.TAPER]
    assert taper

    for planned_day in taper:
        nutrition = report_of(rig_with(plan), planned_day.day).nutrition
        assert nutrition.deficit_kcal == 0
        assert nutrition.target_kcal == nutrition.expenditure_kcal
        assert Advisory.DEFICIT_PAUSED_FOR_RACE in nutrition.advisories


def test_in_the_whole_week_of_an_a_race_even_after_it_there_is_no_deficit() -> None:
    plan = planned(("a", 98, Priority.A), ("c", 130, Priority.C))  # 8 June 2026, a Monday
    race_day = TODAY + timedelta(days=98)
    assert race_day.weekday() == 0
    week = [race_day + timedelta(days=i) for i in range(7)]

    for day in week:
        nutrition = report_of(rig_with(plan), day).nutrition
        assert nutrition.deficit_kcal == 0, day
        assert Advisory.DEFICIT_PAUSED_FOR_RACE in nutrition.advisories


def test_the_week_after_the_race_week_has_a_deficit_again() -> None:
    plan = planned(("a", 98, Priority.A), ("c", 130, Priority.C))
    after = TODAY + timedelta(days=98 + 7)

    assert report_of(rig_with(plan), after).nutrition.deficit_kcal > 0


def test_a_b_race_pauses_its_mini_taper_and_its_week_but_not_the_days_before() -> None:
    plan = planned(("b", 40, Priority.B))  # Saturday 11 April
    race_day = TODAY + timedelta(days=40)
    mini = [d.day for d in plan.days if d.phase is Phase.MINI_TAPER]
    week_start = race_day - timedelta(days=race_day.weekday())

    for day in {*mini, race_day, week_start}:
        nutrition = report_of(rig_with(plan), day).nutrition
        assert nutrition.deficit_kcal == 0, day
    before = week_start - timedelta(days=1)
    assert before not in mini
    assert report_of(rig_with(plan), before).nutrition.deficit_kcal > 0


def test_a_c_race_does_not_pause_the_deficit() -> None:
    plan = planned(("c", 40, Priority.C), ("d", 60, Priority.C))

    for offset in (-3, 0, 2):
        nutrition = report_of(rig_with(plan), TODAY + timedelta(days=40 + offset)).nutrition
        assert Advisory.DEFICIT_PAUSED_FOR_RACE not in nutrition.advisories


def test_without_a_weight_loss_goal_there_is_nothing_to_pause_and_no_advisory() -> None:
    plan = planned(("a", 98, Priority.A))
    rig = rig_with(plan, loss=False)
    day = TODAY + timedelta(days=98)

    report = report_of(rig, day)

    assert report.goal == MaintenanceGoal()
    assert Advisory.DEFICIT_PAUSED_FOR_RACE not in report.nutrition.advisories


def test_the_goal_shown_stays_weight_loss_while_the_deficit_is_paused() -> None:
    plan = planned(("a", 98, Priority.A))

    report = report_of(rig_with(plan), TODAY + timedelta(days=98))

    assert report.goal == LOSS
