# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rapid refuelling between two sessions less than 8 hours apart (T-NUT-04): two carbohydrate
intakes two hours apart, each with the carbohydrates of two hours (1.1 g/kg/h), the first 30
minutes after the end with the protein, the last at least 30 minutes before the next session, all
inside the carbs budget of the day. An intake closer than 90 minutes to a meal or another intake
fuses with it and then is no longer numbered.

By hand for an 80 kg man: one intake is round(1.1 * 2 * 80) = 176 g of carbohydrates, the protein
of the first 32 g (0.4 g/kg). A session of 60 minutes from 14:00 ends at minute 900; the first
intake is at 930 and the second at 1050; with the next session `gap` minutes after the end the
intakes that fit are `(gap - 60) // 120 + 1`, at least one and at most two: one under 180 minutes
(1050 would be less than 30 minutes before the next session), two from 180 on.
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
INTAKE_CARBS, FIRST_PROTEIN = 176, 32  # round(1.1 * 2 * 80) and 0.4 * 80
SESSION_START = 14 * 60
END_OF_SESSION = SESSION_START + 60  # 900
FIRST_INTAKE = END_OF_SESSION + 30  # 930
SECOND_INTAKE = FIRST_INTAKE + 120  # 1050
LAST_BEFORE_NEXT = 30
MAX_INTAKES = 2


def afternoon_session_then(gap: int) -> DayNutrition:
    """One session 14:00-15:00 and the first of the next day `gap` minutes after its end: nothing
    else on the day, and no meal within 90 minutes of the intakes, so they stay unfused."""
    return plan_day(
        DayInput(
            MAN,
            MaintenanceGoal(),
            MealTimes.default(),
            (TrainingSession(SESSION_START, 60.0, 600.0, 80.0),),
            minutes_to_next_session=float(gap),
        )
    )


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
    """The intakes that are still numbered: those that did not fuse with anything."""
    return [m for m in day.meals if m.refuel_intake is not None]


def expected_count(gap: int) -> int:
    return max(1, min(MAX_INTAKES, (gap - 60) // 120 + 1))


# --- By hand --------------------------------------------------------------------------------------
def test_t_nut_04_three_hours_between_two_sessions_give_two_intakes_two_hours_apart_by_hand() -> (
    None
):
    items = refuel(afternoon_session_then(180))

    assert [(m.minute, m.refuel_intake) for m in items] == [
        (FIRST_INTAKE, 1),
        (SECOND_INTAKE, 2),
    ]
    assert [m.macros.carbs_g for m in items] == [INTAKE_CARBS, 77]  # 2nd cut: scarce day
    assert [m.macros.protein_g for m in items] == [FIRST_PROTEIN, 0]  # the protein is in the first
    assert [m.refuel_hours for m in items] == [(1, 2), (3, 4)]
    assert all(m.slots == (MealSlot.POST_TRAINING,) for m in items)  # no meal within 90 minutes


def test_t_nut_04_two_full_intakes_of_176_g_on_a_day_with_enough_carbs_by_hand() -> None:
    day = two_sessions(240)  # 07:00-08:00 and 12:00: the first intake at 510 fuses with breakfast

    second = [m for m in day.meals if m.refuel_intake == 2]
    assert [(m.minute, m.macros.carbs_g, m.macros.protein_g) for m in second] == [(630, 176, 0)]
    fused = [m for m in day.meals if m.slots == (MealSlot.BREAKFAST, MealSlot.POST_TRAINING)]
    assert [(m.minute, m.refuel_intake) for m in fused] == [(540, None)]  # at the latest time
    assert fused[0].macros.protein_g == FIRST_PROTEIN + 16  # the intake's protein and breakfast's
    assert Advisory.INTAKES_SCALED not in day.advisories


@pytest.mark.parametrize("gap", [180, 209, 210, 239, 240, 300, 479])
def test_t_nut_04_from_three_hours_up_to_8_hours_two_intakes_and_no_more(gap: int) -> None:
    items = refuel(afternoon_session_then(gap))

    assert [(m.minute, m.refuel_intake) for m in items] == [
        (FIRST_INTAKE, 1),
        (SECOND_INTAKE, 2),
    ], gap


@pytest.mark.parametrize("gap", [120, 149, 150, 179])
def test_t_nut_04_under_three_hours_the_second_intake_would_be_too_close_to_the_next_session(
    gap: int,
) -> None:
    """At 150 minutes the second intake would be exactly 30 minutes before the next session, but
    it is the first one that waits 30 minutes after the end: it is at end + 150, so the next
    session must be at least 180 minutes after the end."""
    items = refuel(afternoon_session_then(gap))

    assert [(m.minute, m.refuel_intake) for m in items] == [(FIRST_INTAKE, 1)], gap
    assert items[0].macros.carbs_g == INTAKE_CARBS
    assert items[0].macros.protein_g == FIRST_PROTEIN


@pytest.mark.parametrize(
    "gap", [60, 61, 89, 90, 100, 119, 120, 149, 150, 179, 180, 209, 210, 239, 240, 300, 479]
)
def test_t_nut_04_the_last_intake_is_at_least_30_minutes_before_the_next_session(gap: int) -> None:
    items = refuel(afternoon_session_then(gap))
    next_start = END_OF_SESSION + gap

    assert len(items) == expected_count(gap)
    assert [m.minute for m in items] == [FIRST_INTAKE + 120 * k for k in range(len(items))]
    assert all(m.minute <= next_start - LAST_BEFORE_NEXT for m in items[1:])


@pytest.mark.parametrize("gap", [0, 10, 30, 59])
def test_t_nut_04_the_first_intake_is_always_there_even_if_the_sessions_nearly_touch(
    gap: int,
) -> None:
    items = refuel(afternoon_session_then(gap))

    assert [m.refuel_intake for m in items] == [1]
    assert items[0].macros.protein_g == FIRST_PROTEIN
    assert items[0].macros.carbs_g == INTAKE_CARBS


# --- The 8 hours and the length of the session ----------------------------------------------------
def test_t_nut_04_at_8_hours_the_refuelling_is_a_single_dose_and_under_8_hours_it_is_rapid() -> (
    None
):
    assert len(refuel(afternoon_session_then(479))) == MAX_INTAKES
    assert refuel(afternoon_session_then(480)) == []  # 8 hours exactly: not "rapid" any more
    single = [m for m in afternoon_session_then(480).meals if MealSlot.POST_TRAINING in m.slots]
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
    assert all(m.macros.protein_g >= 0 for m in day.meals)


def test_t_nut_04_the_numbered_intakes_are_in_time_order_and_numbered_from_one() -> None:
    for gap in range(60, 480, 7):
        items = refuel(afternoon_session_then(gap))
        assert [m.refuel_intake for m in items] == list(range(1, len(items) + 1)), gap
        assert [m.minute for m in items] == sorted(m.minute for m in items), gap


# --- Fusion with meals ----------------------------------------------------------------------------
def test_t_nut_04_an_intake_near_a_meal_fuses_with_it_and_loses_its_number() -> None:
    day = two_sessions(120)  # first intake at 510, breakfast moved after the session to 540

    fused = [m for m in day.meals if len(m.slots) > 1 and MealSlot.POST_TRAINING in m.slots]
    assert fused, "an intake 30 minutes before a meal fuses with it"
    assert all(m.refuel_intake is None for m in fused)
    assert all(m.refuel_hours is None for m in fused)
    assert sum(m.macros.carbs_g for m in day.meals) == day.macros.carbs_g


@pytest.mark.parametrize("gap", range(0, 480, 13))
def test_t_nut_04_a_fused_item_is_never_numbered_and_a_numbered_one_is_a_lone_post_training(
    gap: int,
) -> None:
    day = two_sessions(gap)

    for item in day.meals:
        if len(item.slots) > 1:
            assert item.refuel_intake is None, (gap, item)
        if item.refuel_intake is not None:
            assert item.slots == (MealSlot.POST_TRAINING,), (gap, item)


def test_t_nut_04_the_numbered_intakes_stay_at_end_plus_30_and_end_plus_150_minutes() -> None:
    for gap in range(0, 480, 11):
        for item in refuel(two_sessions(gap)):
            expected = 480 + 30 + 120 * ((item.refuel_intake or 0) - 1)
            assert item.minute == expected, (gap, item)


# --- Inside the budget of the day -----------------------------------------------------------------
def check_budget(day: DayNutrition, weight_kg: float) -> None:
    total = day.macros
    assert sum(m.macros.carbs_g for m in day.meals) == total.carbs_g
    assert sum(m.macros.protein_g for m in day.meals) == total.protein_g
    assert sum(m.macros.fat_g for m in day.meals) == total.fat_g
    for item in day.meals:
        assert min(item.macros.carbs_g, item.macros.protein_g, item.macros.fat_g) >= 0
        if item.refuel_intake is not None:
            assert item.slots == (MealSlot.POST_TRAINING,)
            assert item.macros.carbs_g <= round(1.1 * 2 * weight_kg)
            assert item.macros.carbs_g + item.macros.protein_g > 0  # an empty intake disappears
        if len(item.slots) > 1:
            assert item.refuel_intake is None  # a fused intake is no longer numbered
    intakes = [m.refuel_intake for m in day.meals if m.refuel_intake is not None]
    assert all(1 <= number <= MAX_INTAKES for number in intakes)
    if Advisory.INTAKES_SCALED not in day.advisories:
        assert all(
            m.macros.carbs_g == round(1.1 * 2 * weight_kg) for m in day.meals if m.refuel_intake
        )  # nothing was cut, so every numbered intake is the full 2 hours at 1.1 g/kg


def test_t_nut_04_a_day_with_plenty_of_carbs_keeps_the_intakes_at_176_g_and_the_total_equal() -> (
    None
):
    day = two_sessions(240)

    check_budget(day, 80.0)
    assert Advisory.INTAKES_SCALED not in day.advisories
    assert [m.macros.carbs_g for m in refuel(day)] == [INTAKE_CARBS]  # 1st fused with breakfast


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
def test_t_nut_04_the_rapid_intakes_stay_inside_the_budget_on_any_day(day_input: DayInput) -> None:
    check_budget(plan_day(day_input), day_input.person.weight_kg)


@pytest.mark.collaudo
@given(days())
@settings(max_examples=20_000, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_the_rapid_intakes_stay_inside_the_budget_on_any_day_long(
    day_input: DayInput,
) -> None:
    check_budget(plan_day(day_input), day_input.person.weight_kg)


@given(st.integers(0, 470), st.integers(20, 120))
@settings(max_examples=200, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_property_the_second_intake_is_30_minutes_before_the_next_session(
    gap: int, minutes: int
) -> None:
    day = two_sessions(gap, first_minutes=minutes)
    first_end = 7 * 60 + minutes
    second = [m for m in refuel(day) if m.refuel_intake == 2]
    assume(second)
    assert all(m.minute == first_end + 30 + 120 for m in second)
    assert all(m.minute <= first_end + gap - LAST_BEFORE_NEXT for m in second)


@given(st.integers(0, 470), st.integers(30, 120))
@settings(max_examples=200, **_FAST)  # type: ignore[arg-type]
def test_t_nut_04_property_the_first_intake_is_always_given_after_the_first_session(
    gap: int, minutes: int
) -> None:
    """Numbered or fused with a meal, something with POST_TRAINING exists no earlier than 30
    minutes after the end of the first session (a fused item sits at the later time)."""
    first_end = 7 * 60 + minutes
    day = two_sessions(gap, first_minutes=minutes)

    after_first = [
        m for m in day.meals if MealSlot.POST_TRAINING in m.slots and m.minute >= first_end + 30
    ]
    assert after_first
    assert sum(m.macros.protein_g for m in after_first) >= FIRST_PROTEIN


def test_t_nut_04_late_evening_intakes_do_not_pile_up_on_the_same_minute() -> None:
    evening = TrainingSession(21 * 60 + 30, 60.0, 600.0, 80.0)  # ends at 22:30
    day = plan_day(
        DayInput(
            MAN, MaintenanceGoal(), MealTimes.default(), (evening,), minutes_to_next_session=330.0
        )
    )

    items = refuel(day)

    assert [(m.minute, m.refuel_intake) for m in items] == [(23 * 60, 1)]  # 01:00 is left out
    assert items[0].macros.carbs_g == INTAKE_CARBS


def test_t_nut_04_a_session_ending_at_23_40_has_its_first_intake_stopped_at_23_59() -> None:
    late = TrainingSession(22 * 60 + 40, 60.0, 600.0, 80.0)  # ends at 23:40, first at 00:10
    day = plan_day(
        DayInput(
            MAN, MaintenanceGoal(), MealTimes.default(), (late,), minutes_to_next_session=330.0
        )
    )

    assert [(m.minute, m.refuel_intake) for m in refuel(day)] == [(1439, 1)]


# --- On the real stack ----------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    opened = make_world(tmp_path / "w")
    yield opened
    opened.close()


def test_t_nut_04_two_runs_of_the_same_day_on_real_files_give_the_rapid_intakes_by_hand(
    world: World,
) -> None:
    day = TODAY - timedelta(days=2)
    world.import_run(day, minutes=60, bpm=150, hour=7)  # 07:00-08:00
    world.import_run(day, minutes=60, bpm=150, hour=11)  # starts 11:00: 180 minutes after the end

    report = world.report(day)

    items = [m for m in report.nutrition.meals if m.refuel_intake is not None]
    assert [(m.minute, m.refuel_intake) for m in items] == [(630, 2)]  # the first fused: 540
    assert items[0].macros.carbs_g == INTAKE_CARBS
    fused = [m for m in report.nutrition.meals if m.minute == 540]
    assert [m.slots for m in fused] == [(MealSlot.BREAKFAST, MealSlot.POST_TRAINING)]
    check_budget(report.nutrition, 80.0)


def test_t_nut_04_an_evening_run_followed_by_an_early_one_the_next_day_is_refuelled_rapidly(
    world: World,
) -> None:
    day = TODAY - timedelta(days=3)
    world.import_run(day, minutes=60, bpm=150, hour=19)  # ends at 20:00
    world.import_run(day + timedelta(days=1), minutes=60, bpm=150, hour=3)  # 3 hours after midnight

    report = world.report(day)

    posts = [m for m in report.nutrition.meals if MealSlot.POST_TRAINING in m.slots]
    assert 1 <= len(posts) <= MAX_INTAKES
    assert posts[0].minute >= 20 * 60 + 30  # nothing before the first intake's time
    assert sum(m.macros.protein_g for m in posts) >= FIRST_PROTEIN
    check_budget(report.nutrition, 80.0)
