# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Hourly refuelling between two sessions less than 8 hours apart (T-NUT-04): one carbohydrate
intake every hour for the first four hours, 1.1 g/kg each, the first 30 minutes after the end, the
protein in the first, the last at least 30 minutes before the next session, all inside the carbs
budget of the day.

By hand for an 80 kg man: one intake is 88 g of carbohydrates, the protein of the first 32 g
(0.4 g/kg). A session of 60 minutes from 07:00 ends at minute 480; the first intake is at 510 and
the k-th at 510 + 60k; with the next session starting `gap` minutes after the end the intakes that
fit are `(gap - 60) // 60 + 1`, at least one and at most four.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    Advisory,
    DayInput,
    DayNutrition,
    MaintenanceGoal,
    MealItem,
    MealSlot,
    MealTimes,
    PerformanceGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
    plan_day,
)
from ponos_coach.nutrition.domain.settings import ActivityLevel
from tests.collaudo.fase5_support import TODAY, World, make_world

MAN = Person(Sex.MALE, 35, 180, 80.0)
INTAKE_CARBS, FIRST_PROTEIN = 88, 32
END_OF_FIRST = 7 * 60 + 60  # 480
LAST_BEFORE_NEXT = 30


def two_sessions(
    gap: int, *, first_minutes: int = 60, second_minutes: int = 60, person: Person = MAN
) -> DayNutrition:
    first = TrainingSession(7 * 60, float(first_minutes), 600.0, 80.0)
    start2 = int(first.end_minute + gap)
    return plan_day(
        DayInput(
            person,
            MaintenanceGoal(),
            MealTimes.default(),
            (first, TrainingSession(start2, float(second_minutes), 600.0, 80.0)),
        )
    )


def refuel(day: DayNutrition) -> list[MealItem]:
    return [m for m in day.meals if m.refuel_hour is not None]


def expected_count(gap: int) -> int:
    return max(1, min(4, (gap - 60) // 60 + 1))


# --- By hand --------------------------------------------------------------------------------------
def test_t_nut_04_two_hours_between_two_sessions_give_two_hourly_intakes_by_hand() -> None:
    items = refuel(two_sessions(120))

    assert [(m.minute, m.refuel_hour) for m in items] == [(510, 1), (570, 2)]
    assert [m.macros.carbs_g for m in items] == [INTAKE_CARBS, INTAKE_CARBS]
    assert [m.macros.protein_g for m in items] == [FIRST_PROTEIN, 0]  # the protein is in the first
    assert all(m.slots == (MealSlot.POST_TRAINING,) for m in items)  # never fused with a meal


def test_t_nut_04_four_hours_or_more_give_four_intakes_and_no_more() -> None:
    for gap in (240, 300, 479):
        items = refuel(two_sessions(gap))
        assert [(m.minute, m.refuel_hour) for m in items] == [
            (510, 1), (570, 2), (630, 3), (690, 4),
        ], gap  # fmt: skip


@pytest.mark.parametrize(
    "gap",
    [
        60, 61, 89, 120, 149, 180, 209, 240, 300, 479,
        90,
        100,
        119,
        150,
        179,
        210,
        239,
    ],
)  # fmt: skip
def test_t_nut_04_the_last_hourly_intake_is_at_least_30_minutes_before_the_next_session(
    gap: int,
) -> None:
    """D-F5-03: `_refuel_hours` subtracts the 30 minutes before the next session from the time
    available but not the 30 minutes after the end that the first intake waits: with a gap of 90
    the second intake is at the very minute the next session starts (nutrition/domain/meals.py,
    `_refuel_hours`)."""
    items = refuel(two_sessions(gap))
    next_start = END_OF_FIRST + gap

    assert len(items) == expected_count(gap)
    assert [m.minute for m in items] == [510 + 60 * k for k in range(len(items))]
    assert all(m.minute <= next_start - LAST_BEFORE_NEXT for m in items[1:])


@pytest.mark.parametrize("gap", [0, 10, 30, 59])
def test_t_nut_04_the_first_intake_is_always_there_even_if_the_sessions_nearly_touch(
    gap: int,
) -> None:
    items = refuel(two_sessions(gap))

    assert [m.refuel_hour for m in items] == [1]
    assert items[0].macros.protein_g == FIRST_PROTEIN


# --- The 8 hours and the length of the session ----------------------------------------------------
def test_t_nut_04_at_8_hours_the_refuelling_is_a_single_dose_and_under_8_hours_it_is_hourly() -> (
    None
):
    assert len(refuel(two_sessions(479))) == 4
    assert refuel(two_sessions(480)) == []  # 8 hours exactly: not "rapid" any more
    single = [m for m in two_sessions(480).meals if MealSlot.POST_TRAINING in m.slots]
    assert single, "a dose after the first session"


def test_t_nut_04_a_session_under_30_minutes_needs_no_intake_at_all() -> None:
    day = plan_day(
        DayInput(
            MAN,
            MaintenanceGoal(),
            MealTimes.default(),
            (
                TrainingSession(7 * 60, 29.0, 200.0, 20.0),
                TrainingSession(9 * 60, 60.0, 600.0, 80.0),
            ),
        )
    )

    assert refuel(day) == []


def test_t_nut_04_the_intakes_are_in_time_order_and_numbered_from_one() -> None:
    for gap in range(60, 480, 7):
        items = refuel(two_sessions(gap))
        assert [m.refuel_hour for m in items] == list(range(1, len(items) + 1)), gap
        assert [m.minute for m in items] == sorted(m.minute for m in items), gap


# --- Inside the budget of the day -----------------------------------------------------------------
def check_budget(day: DayNutrition, weight_kg: float) -> None:
    total = day.macros
    assert sum(m.macros.carbs_g for m in day.meals) == total.carbs_g
    assert sum(m.macros.protein_g for m in day.meals) == total.protein_g
    assert sum(m.macros.fat_g for m in day.meals) == total.fat_g
    for item in day.meals:
        assert min(item.macros.carbs_g, item.macros.protein_g, item.macros.fat_g) >= 0
        if item.refuel_hour is not None:
            assert item.slots == (MealSlot.POST_TRAINING,)
            assert item.macros.carbs_g <= round(1.1 * weight_kg)
            assert item.macros.carbs_g + item.macros.protein_g > 0  # an empty hour disappears
    hours = [m.refuel_hour for m in day.meals if m.refuel_hour is not None]
    assert all(1 <= hour <= 4 for hour in hours)
    if Advisory.INTAKES_SCALED not in day.advisories:
        assert all(
            m.macros.carbs_g == round(1.1 * weight_kg) for m in day.meals if m.refuel_hour
        )  # nothing was cut, so every hour is the full 1.1 g/kg


def test_t_nut_04_a_day_with_plenty_of_carbs_keeps_every_hour_at_88_g_and_the_total_equal() -> None:
    day = two_sessions(240)

    check_budget(day, 80.0)
    assert Advisory.INTAKES_SCALED not in day.advisories
    assert [m.macros.carbs_g for m in refuel(day)] == [INTAKE_CARBS] * 4


def test_t_nut_04_a_scarce_day_serves_during_then_after_then_before_and_keeps_the_total() -> None:
    small = Person(Sex.FEMALE, 40, 160, 50.0, ActivityLevel.SEDENTARY)
    day = plan_day(
        DayInput(
            small,
            WeightLossGoal(500),
            MealTimes.default(),
            (
                TrainingSession(7 * 60, 90.0, 700.0, 120.0),
                TrainingSession(13 * 60, 90.0, 700.0, 120.0),
            ),
        )
    )

    check_budget(day, 50.0)
    assert Advisory.INTAKES_SCALED in day.advisories
    during = [m for m in day.meals if MealSlot.DURING_TRAINING in m.slots]
    assert all(m.macros.carbs_g > 0 for m in during)  # "during" is served first
    lunch_dinner = [m for m in day.meals if m.slots[0] in (MealSlot.LUNCH, MealSlot.DINNER)]
    assert all(m.macros.carbs_g >= 50 for m in lunch_dinner)  # the main meals keep 1 g/kg


_people = st.builds(
    Person,
    st.sampled_from(list(Sex)),
    st.integers(18, 70),
    st.integers(150, 200),
    st.floats(45.0, 120.0).map(lambda w: round(w, 1)),
    st.one_of(st.none(), st.sampled_from(list(ActivityLevel))),
)
_goals = st.one_of(
    st.just(MaintenanceGoal()),
    st.just(PerformanceGoal()),
    st.integers(100, 1000).map(WeightLossGoal),
)


@st.composite
def days(draw: st.DrawFn) -> DayInput:
    person = draw(_people)
    start = draw(st.integers(4 * 60, 15 * 60))
    first = TrainingSession(
        start,
        float(draw(st.integers(20, 180))),
        draw(st.floats(100, 1500)),
        draw(st.floats(10, 300)),
    )
    gap = draw(st.integers(0, 470))
    second_start = int(first.end_minute + gap)
    sessions = [first]
    if second_start <= 1439 and draw(st.booleans()):
        sessions.append(
            TrainingSession(
                second_start,
                float(draw(st.integers(20, 150))),
                draw(st.floats(100, 1500)),
                draw(st.floats(10, 300)),
            )
        )
    next_day = draw(st.one_of(st.none(), st.floats(0, 900)))
    return DayInput(person, draw(_goals), MealTimes.default(), tuple(sessions), next_day)


_FAST = {"deadline": None, "suppress_health_check": [HealthCheck.too_slow]}


@given(days())
@settings(max_examples=400, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_the_hourly_intakes_stay_inside_the_budget_on_any_day(day_input: DayInput) -> None:
    check_budget(plan_day(day_input), day_input.person.weight_kg)


@pytest.mark.collaudo
@given(days())
@settings(max_examples=20_000, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_the_hourly_intakes_stay_inside_the_budget_on_any_day_long(
    day_input: DayInput,
) -> None:
    check_budget(plan_day(day_input), day_input.person.weight_kg)


@given(st.integers(0, 470), st.integers(20, 120))
@settings(max_examples=200, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_property_the_last_hourly_intake_is_30_minutes_before_the_next_session(
    gap: int, minutes: int
) -> None:
    day = two_sessions(gap, first_minutes=minutes)
    first_end = 7 * 60 + minutes
    items = refuel(day)
    assume(len(items) > 1)
    assert all(m.minute <= first_end + gap - LAST_BEFORE_NEXT for m in items[1:])


def test_t_nut_04_late_evening_intakes_do_not_pile_up_on_the_same_minute() -> None:
    evening = TrainingSession(21 * 60 + 30, 60.0, 600.0, 80.0)  # ends at 22:30
    day = plan_day(
        DayInput(
            MAN, MaintenanceGoal(), MealTimes.default(), (evening,), minutes_to_next_session=330.0
        )
    )

    minutes = [m.minute for m in refuel(day)]

    assert len(minutes) == len(set(minutes)), minutes


# --- On the real stack ----------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    opened = make_world(tmp_path / "w")
    yield opened
    opened.close()


def test_t_nut_04_two_runs_of_the_same_day_on_real_files_give_the_hourly_intakes_by_hand(
    world: World,
) -> None:
    day = TODAY - timedelta(days=2)
    world.import_run(day, minutes=60, bpm=150, hour=7)  # 07:00-08:00
    world.import_run(day, minutes=60, bpm=150, hour=10)  # starts 10:00: 120 minutes after the end

    report = world.report(day)

    items = [m for m in report.nutrition.meals if m.refuel_hour is not None]
    assert [(m.minute, m.refuel_hour) for m in items] == [(510, 1), (570, 2)]
    assert all(m.macros.carbs_g == INTAKE_CARBS for m in items)
    assert items[0].macros.protein_g == FIRST_PROTEIN
    check_budget(report.nutrition, 80.0)


def test_t_nut_04_an_evening_run_followed_by_an_early_one_the_next_day_is_refuelled_hourly(
    world: World,
) -> None:
    day = TODAY - timedelta(days=3)
    world.import_run(day, minutes=60, bpm=150, hour=19)  # ends at 20:00
    world.import_run(day + timedelta(days=1), minutes=60, bpm=150, hour=3)  # 3 hours after midnight

    report = world.report(day)

    items = [m for m in report.nutrition.meals if m.refuel_hour is not None]
    assert 1 <= len(items) <= 4
    assert items[0].minute == 20 * 60 + 30
    check_budget(report.nutrition, 80.0)
