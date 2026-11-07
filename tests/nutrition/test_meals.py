# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-04: meals and training moments (dawn, lunch, late evening, two sessions, long session)."""

from __future__ import annotations

from datetime import time

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.nutrition.domain import (
    DayInput,
    HourlyIntake,
    Macros,
    MealItem,
    MealSlot,
    MealTimes,
    TrainingSession,
    plan_day,
    schedule_meals,
    split_integer,
)
from tests.nutrition.strategies import days, meal_times, sessions

DAY = Macros(400, 120, 80)
KG = 80.0
DEFAULT_TIMES = MealTimes.default()  # 07:30 (450), 13:00 (780), 20:00 (1200)
B, L, D = MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER
PRE, DURING, POST = MealSlot.PRE_TRAINING, MealSlot.DURING_TRAINING, MealSlot.POST_TRAINING


def session(start: int, minutes: float) -> TrainingSession:
    return TrainingSession(start, minutes, 300.0, 50.0)


def schedule(
    *training: TrainingSession,
    day: Macros = DAY,
    times: MealTimes = DEFAULT_TIMES,
    next_in: float | None = None,
) -> tuple[MealItem, ...]:
    items, _ = schedule_meals(day, KG, times, training, next_in)
    return items


def shape(items: tuple[MealItem, ...]) -> list[tuple[int, tuple[MealSlot, ...]]]:
    return [(item.minute, item.slots) for item in items]


def total(items: tuple[MealItem, ...]) -> Macros:
    result = Macros()
    for item in items:
        result = result + item.macros
    return result


# --- A day without training ------------------------------------------------------------------


def test_a_rest_day_is_split_over_the_three_meals_at_their_typical_times() -> None:
    items = schedule()

    assert shape(items) == [(450, (B,)), (780, (L,)), (1200, (D,))]
    assert [item.macros for item in items] == [
        Macros(100, 30, 20),  # 25% of the day
        Macros(160, 48, 32),  # 40%
        Macros(140, 42, 28),  # 35%
    ]
    assert not any(item.moved for item in items)


def test_a_short_session_needs_no_intake_and_moves_no_meal() -> None:
    items = schedule(session(780, 25.0))  # right at lunch time, under 30 minutes

    assert shape(items) == [(450, (B,)), (780, (L,)), (1200, (D,))]


# --- Dawn, lunch, late evening ---------------------------------------------------------------


def test_a_session_at_dawn_before_breakfast_merges_the_recovery_with_breakfast() -> None:
    items = schedule(session(5 * 60 + 30, 60.0))  # 05:30-06:30; post-training at 07:00

    assert shape(items) == [(450, (B, POST)), (780, (L,)), (1200, (D,))]
    assert items[0].macros.protein_g >= 24  # 0.3 g/kg x 80 kg from the recovery, plus breakfast


def test_a_session_at_lunch_time_moves_lunch_after_it_and_merges_it_with_the_recovery() -> None:
    items = schedule(session(12 * 60 + 30, 60.0))  # 12:30-13:30; recovery at 14:00

    assert shape(items) == [(450, (B,)), (870, (L, POST)), (1200, (D,))]
    assert items[1].moved


def test_a_session_late_in_the_evening_moves_dinner_before_it() -> None:
    late_dinner = MealTimes(time(7, 30), time(13, 0), time(21, 0))

    items = schedule(session(21 * 60 + 30, 60.0), times=late_dinner)  # 21:30-22:30

    assert shape(items) == [(450, (B,)), (780, (L,)), (1230, (D,)), (1380, (POST,))]
    assert items[2].moved  # dinner at 20:30, an hour before the start


def test_dinner_after_a_session_goes_just_after_the_blocked_hour() -> None:
    items = schedule(session(20 * 60 + 30, 90.0))  # 20:30-22:00, dinner at 20:00 is too close

    dinner = next(item for item in items if D in item.slots)
    assert dinner.minute == 23 * 60  # one hour after the end, the latest allowed
    assert dinner.moved


def test_a_dawn_session_longer_than_an_hour_eats_something_an_hour_before() -> None:
    items = schedule(session(5 * 60, 120.0))  # 05:00-07:00

    assert shape(items) == [
        (240, (PRE,)),  # 2 h before would be 03:00, too early: 1 h before
        (300, (DURING,)),
        (480, (B, POST)),  # breakfast moved after the blocked hour, joined by the recovery
        (780, (L,)),
        (1200, (D,)),
    ]
    assert items[2].moved


# --- Two sessions in one day -----------------------------------------------------------------


def test_two_sessions_in_a_day_each_get_their_recovery_and_meals_move_around_them() -> None:
    items = schedule(session(7 * 60, 60.0), session(18 * 60, 60.0))

    assert shape(items) == [(540, (B, POST)), (780, (L,)), (1200, (D, POST))]
    # 10 h separate the sessions (08:00 to 18:00), more than the 8 h of rapid refuelling: neither
    # recovery has carbohydrates, so the day's 400 g are split 25/40/35 = 100/160/140
    # (the protein after a session is 0.4 g/kg: 32 g each, the rest of the 120 g is split 25/40/35)
    assert [item.macros for item in items] == [
        Macros(100, 14 + 32, 20),
        Macros(160, 22, 32),
        Macros(140, 20 + 32, 28),
    ]
    assert items[0].moved
    assert not items[2].moved


def test_sessions_less_than_8_hours_apart_give_the_first_recovery_hourly_carbohydrates() -> None:
    items = schedule(session(7 * 60, 60.0), session(15 * 60, 60.0))

    # 08:00 to 15:00 is 7 h: rapid refuelling, 88 g of carbohydrates (1.1 g/kg x 80 kg) an hour for
    # 4 hours would be 352 g, but lunch and dinner keep their 80 g each (1 g/kg) out of the day's
    # 400 g: 240 g are left for the intakes, given in time order (88, 88, 64, none for the fourth
    # hour); the main meals share the rest as they do today
    assert [(i.minute, i.refuel_hour, i.macros.carbs_g) for i in items if i.refuel_hour] == [
        (8 * 60 + 30, 1, 88),
        (9 * 60 + 30, 2, 88),
        (10 * 60 + 30, 3, 64),
    ]
    assert [i.macros.carbs_g for i in items if i.slots in ((B,), (L,), (D,))] == [0, 80, 80]
    assert total(items) == DAY


def test_the_hourly_recovery_is_one_intake_an_hour_with_the_protein_in_the_first() -> None:
    big = Macros(900, 120, 80)

    items = schedule(session(7 * 60, 60.0), session(15 * 60, 60.0), day=big)

    refuel = [i for i in items if i.refuel_hour]
    assert [(i.refuel_hour, i.minute) for i in refuel] == [(1, 510), (2, 570), (3, 630), (4, 690)]
    assert [i.macros.carbs_g for i in refuel] == [88] * 4  # 1.1 g/kg x 80 kg, every hour
    assert [i.macros.protein_g for i in refuel] == [32, 0, 0, 0]  # 0.4 g/kg once
    assert all(i.slots == (POST,) for i in refuel)
    assert total(items) == big


def test_hourly_intakes_are_never_merged_with_a_meal_or_with_each_other() -> None:
    # lunch (13:00) falls in the 4 hours after a session that ends at 10:30: it stays a meal
    items = schedule(session(9 * 60 + 30, 60.0), session(16 * 60, 60.0), day=Macros(900, 120, 80))

    refuel = [i for i in items if i.refuel_hour]
    assert [i.minute for i in refuel] == [660, 720, 780, 840]  # 11:00, 12:00, 13:00, 14:00
    assert all(i.slots == (POST,) for i in refuel)
    assert any(L in i.slots and i.refuel_hour is None for i in items)


@pytest.mark.parametrize(
    ("next_in", "hours"),
    [
        (None, 0),  # no next session: one dose, no hourly intakes
        (481.0, 0),
        (480.0, 0),  # exactly 8 h: "less than 8 hours" is false
        (479.0, 4),
        (210.0, 4),  # four hours fit and the last is 30 minutes before the session
        (209.0, 3),
        (150.0, 3),
        (90.0, 2),
        (89.0, 1),
        (60.0, 1),
        (0.0, 1),  # the first intake is always given
    ],
)
def test_hourly_intakes_stop_30_minutes_before_the_next_session_and_after_4_hours(
    next_in: float | None, hours: int
) -> None:
    items = schedule(session(10 * 60, 60.0), day=Macros(1200, 120, 80), next_in=next_in)

    refuel = [i for i in items if i.refuel_hour]
    assert [i.refuel_hour for i in refuel] == list(range(1, hours + 1))
    if hours == 0:
        # a single dose after a short session: protein only
        (post,) = [i for i in items if POST in i.slots]
        assert post.macros.protein_g == 32


def test_the_order_in_which_sessions_are_given_does_not_matter() -> None:
    morning, evening = session(7 * 60, 60.0), session(18 * 60, 60.0)

    assert schedule(evening, morning) == schedule(morning, evening)


def test_the_hourly_intakes_come_out_of_the_budget_of_the_days_carbohydrates() -> None:
    items = schedule(session(18 * 60, 60.0), next_in=479.0)  # a day of 400 g carbohydrates

    assert total(items) == DAY
    refuel = [i for i in items if i.refuel_hour]
    assert sum(i.macros.carbs_g for i in refuel) == 240  # 400 g minus 80 g each for lunch, dinner
    assert [i.macros.carbs_g for i in items if i.slots in ((L,), (D,))] == [80, 80]


# --- A long session --------------------------------------------------------------------------


def test_a_long_session_has_pre_during_and_post_with_hourly_guidance() -> None:
    day = Macros(800, 130, 90)

    items = schedule(session(8 * 60, 180.0), day=day)  # 08:00-11:00

    assert shape(items) == [
        (360, (PRE,)),
        (480, (DURING,)),
        (780, (B, L, POST)),  # breakfast and the recovery fall inside or right after the session
        (1200, (D,)),
    ]
    assert items[0].macros == Macros(80, 0, 0)  # 1 g/kg
    assert items[1].macros == Macros(180, 0, 0)  # 60 g/h for 3 h
    assert items[1].hourly == HourlyIntake(60, 600, 300)
    assert items[2].macros.carbs_g >= 88  # recovery: 1.1 g/kg carbohydrates, plus the two meals
    assert items[2].moved
    assert total(items) == day


@pytest.mark.parametrize(
    ("minutes", "has_during", "carbs_per_hour"),
    [
        (75.0, False, None),  # on the threshold: not longer than 75 minutes
        (76.0, True, 45),
        (150.0, True, 45),  # up to 2.5 h
        (151.0, True, 60),  # beyond 2.5 h: the 60 g/h ceiling of a single carbohydrate
    ],
)
def test_the_intake_during_the_session_starts_beyond_75_minutes(
    minutes: float, has_during: bool, carbs_per_hour: int | None
) -> None:
    items = schedule(session(10 * 60, minutes))

    during = [i for i in items if DURING in i.slots]
    assert bool(during) is has_during
    if has_during:
        assert during[0].hourly is not None
        assert during[0].hourly.carbs_g == carbs_per_hour


@pytest.mark.parametrize(("minutes", "has_pre"), [(60.0, False), (61.0, True)])
def test_the_pre_training_intake_starts_beyond_one_hour(minutes: float, has_pre: bool) -> None:
    items = schedule(session(10 * 60, minutes))

    assert any(PRE in i.slots for i in items) is has_pre


@pytest.mark.parametrize(("minutes", "carbs"), [(89.0, 0), (90.0, 88)])
def test_recovery_carbohydrates_start_with_a_long_session(minutes: float, carbs: int) -> None:
    items = schedule(session(16 * 60, minutes))

    (post,) = [i for i in items if i.slots == (POST,)]
    assert post.macros == Macros(carbs, 32, 0)  # 0.4 g/kg of protein


def test_intakes_are_cut_to_fit_a_day_that_cannot_hold_them() -> None:
    small = Macros(100, 20, 50)

    items, scaled = schedule_meals(small, KG, DEFAULT_TIMES, (session(8 * 60, 240.0),), None)

    assert scaled
    assert total(items) == small


def test_a_day_too_small_for_every_intake_serves_the_hours_during_first_and_keeps_dinner() -> None:
    # 4 h from 08:00 would ask 240 g during (60 g/h), 80 before and 88 after: 408 g out of 300.
    # Lunch and dinner keep 80 g each (1 g/kg), so 140 g are left for the intakes: all of them to
    # the hours during (the box becomes 140 / 4 h = 35 g/h), none to the recovery and the pre.
    day = Macros(300, 100, 60)

    items, scaled = schedule_meals(day, KG, DEFAULT_TIMES, (session(8 * 60, 240.0),), None)

    assert scaled
    assert total(items) == day
    (during,) = [i for i in items if DURING in i.slots]
    assert during.macros == Macros(140, 0, 0)
    assert during.hourly == HourlyIntake(35, 600, 300)
    assert during.macros.carbs_g == during.hourly.carbs_g * 4  # the box and the sum agree
    (dinner,) = [i for i in items if D in i.slots]
    assert dinner.macros.carbs_g == 80
    lunch_carbs = sum(i.macros.carbs_g for i in items if L in i.slots)
    assert lunch_carbs == 80


def test_a_day_with_room_for_every_intake_is_not_cut() -> None:
    items, scaled = schedule_meals(
        Macros(800, 130, 90), KG, DEFAULT_TIMES, (session(8 * 60, 180.0),), None
    )

    assert not scaled
    (during,) = [i for i in items if DURING in i.slots]
    assert during.macros.carbs_g == 180
    assert during.hourly == HourlyIntake(60, 600, 300)


def test_the_main_meals_keep_at_least_1_g_per_kg_of_carbohydrates_taking_from_breakfast() -> None:
    # 200 g, 80 kg: split 50 / 80 / 70 would leave dinner under 80 g; breakfast gives 10
    items = schedule(day=Macros(200, 60, 40))

    carbs = {item.slots: item.macros.carbs_g for item in items}
    assert carbs == {(B,): 40, (L,): 80, (D,): 80}


def test_the_main_meal_minimum_gives_way_when_the_day_has_less_carbohydrate_than_two_minimums() -> (
    None
):
    items = schedule(day=Macros(100, 60, 40))  # 100 g: 50 g each for lunch and dinner at most

    carbs = {item.slots: item.macros.carbs_g for item in items}
    assert carbs == {(B,): 0, (L,): 50, (D,): 50}


def test_a_session_ends_where_the_wall_clock_says_not_after_the_moving_time() -> None:
    # 10:00, 60 min moving, but on the clock until 14:00 (long stops): the recovery comes at
    # 14:30 and lunch (13:00) is inside the hour around the session, so it goes after it, to
    # 15:00, and merges with the recovery
    stopped = TrainingSession(10 * 60, 60.0, 300.0, 50.0, local_end_minute=14 * 60.0)
    plain = TrainingSession(10 * 60, 60.0, 300.0, 50.0)

    with_stops = {i.slots: i for i in schedule(stopped)}
    without = {i.slots: i for i in schedule(plain)}

    assert with_stops[(L, POST)].minute == 15 * 60
    assert with_stops[(L, POST)].moved
    assert without[(POST,)].minute == 11 * 60 + 30  # the moving time alone ends at 11:00
    assert without[(L,)].minute == 780


@pytest.mark.parametrize("end", [599.0, float("nan"), float("inf")])
def test_a_session_cannot_end_before_it_starts_nor_at_a_non_finite_minute(end: float) -> None:
    with pytest.raises(ValueError, match="end"):
        TrainingSession(600, 60.0, 300.0, 50.0, local_end_minute=end)


# --- Invariants (property tests) -------------------------------------------------------------


@given(days)
def test_the_items_always_sum_to_the_day_with_no_negative_amount(day: DayInput) -> None:
    result = plan_day(day)

    assert total(result.meals) == result.macros
    for item in result.meals:
        assert min(item.macros.carbs_g, item.macros.protein_g, item.macros.fat_g) >= 0
        assert 0 <= item.minute <= 1439


@given(days)
def test_items_are_sorted_by_time_and_each_regular_meal_appears_once(day: DayInput) -> None:
    result = plan_day(day)

    minutes = [item.minute for item in result.meals]
    assert minutes == sorted(minutes)
    slots = [slot for item in result.meals for slot in item.slots]
    for regular in (B, L, D):
        assert slots.count(regular) == 1


@given(days)
def test_every_session_long_enough_has_a_recovery_and_only_those_that_need_it_an_intake_during(
    day: DayInput,
) -> None:
    result = plan_day(day)

    slots = [slot for item in result.meals for slot in item.slots]
    worth = [s for s in day.sessions if s.duration_min >= 30.0]
    assert (POST in slots) == bool(worth)
    assert slots.count(DURING) <= len([s for s in worth if s.duration_min > 75.0])


@given(days)
def test_a_lone_regular_meal_is_never_left_inside_the_hour_around_a_session(
    day: DayInput,
) -> None:
    result = plan_day(day)

    for item in result.meals:
        if item.slots not in ((B,), (L,), (D,)):
            continue
        for s in day.sessions:
            inside = s.start_minute - 60 < item.minute < s.end_minute + 60
            assert not inside or s.duration_min < 30.0 or item.minute == 1439  # the end of the day


@given(st.integers(0, 5000), st.lists(st.floats(0.01, 100.0), min_size=1, max_size=6))
def test_splitting_whole_grams_always_sums_exactly(total_g: int, weights: list[float]) -> None:
    parts = split_integer(total_g, weights)

    assert sum(parts) == total_g
    assert min(parts) >= 0


@given(meal_times(), st.lists(sessions, max_size=3))
def test_the_same_inputs_always_give_the_same_items(
    times: MealTimes, training: list[TrainingSession]
) -> None:
    assert schedule(*training, times=times) == schedule(*training, times=times)


def test_splitting_follows_the_weights_and_gives_the_leftover_to_the_largest_remainder() -> None:
    assert split_integer(10, [1.0, 1.0, 1.0]) == [4, 3, 3]
    assert split_integer(100, [0.25, 0.40, 0.35]) == [25, 40, 35]
    assert split_integer(0, [1.0, 2.0]) == [0, 0]


@pytest.mark.parametrize(
    ("total_g", "weights"), [(-1, [1.0]), (5, []), (5, [0.0, 0.0]), (5, [-1.0, 2.0])]
)
def test_splitting_rejects_impossible_requests(total_g: int, weights: list[float]) -> None:
    with pytest.raises(ValueError, match="needed"):
        split_integer(total_g, weights)
