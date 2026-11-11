# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The calendar, the day page and the nutrition settings from outside (T-UI-01, 05, 06, 07).

Without a window: what the day page shows is checked against the model for every kind of person
and day, in four locales (every number on screen is a number the user can add up); the month grid
is checked against the activities and the goal periods it is built from; the limits of the
settings are tried at their edges. With the real window: the settings and the guidance survive a
restart of the application.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PySide6.QtCore import QDate, QLocale, QTime
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    Advisory,
    GoalPeriod,
    MaintenanceGoal,
    PerformanceGoal,
    Person,
    WeightLossGoal,
    goal_for_day,
)
from ponos_coach.profiles.day_nutrition import NutritionUnavailable, UnavailableReason
from ponos_coach.ui.calendar_model import (
    GoalKind,
    build_month,
    first_of_month,
    goal_kind_of,
    shift_month,
)
from ponos_coach.ui.day_presentation import DayView, UnavailableView, present_day
from ponos_coach.ui.nutrition_settings_model import (
    MealField,
    PeriodProblem,
    goal_of,
    meal_time_problems,
    meal_times_of,
    pace_in_grams,
    pace_of,
    period_issue,
)
from ponos_coach.ui.nutrition_texts import advisory_text, unavailable_text
from tests.collaudo.test_fase4b_modello import PEOPLE, SCENARIOS, run
from tests.ui.helpers import profile_activity
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    EMPTY_DAY,
    last_day_page,
    open_day,
    open_settings,
    plain,
    window_with_days,
)
from tests.ui.test_day_presentation import report_for
from tests.ui.workspace_support import WAIT_MS, close_window, compose, make_settings, wait_ready

LOCALES = [
    QLocale(QLocale.Language.English, QLocale.Country.UnitedStates),
    QLocale(QLocale.Language.Italian, QLocale.Country.Italy),
    QLocale(QLocale.Language.German, QLocale.Country.Germany),
    QLocale(QLocale.Language.French, QLocale.Country.France),
]
DAY = date(2026, 6, 10)


@pytest.fixture
def locale_restored() -> Iterator[None]:
    previous = QLocale()
    yield
    QLocale.setDefault(previous)


def number(text: str) -> int:
    """The whole number written in the current locale (the thousands separator included)."""
    value, ok = QLocale().toInt(text)
    assert ok, text
    return value


# --- The day page agrees with the model, in every locale --------------------------------------


@pytest.mark.parametrize("locale", LOCALES, ids=lambda loc: loc.name())
@pytest.mark.parametrize("person", PEOPLE, ids=lambda p: f"{p.sex.value}-{p.age_years}")
@pytest.mark.parametrize("scenario", ["no session", "dawn before breakfast", "ultra of 8 hours"])
def test_t_ui_06_every_number_of_the_day_page_adds_up_in_every_locale(
    locale_restored: None, locale: QLocale, person: Person, scenario: str
) -> None:
    QLocale.setDefault(locale)
    report = report_for(person, WeightLossGoal(500), SCENARIOS[scenario])

    view = present_day(DAY, report)

    assert isinstance(view, DayView)
    totals = {label: number(value) for label, value in view.totals}
    carbs = sum(number(card.carbs) for card in view.meals)
    protein = sum(number(card.protein) for card in view.meals)
    fat = sum(number(card.fat) for card in view.meals)
    assert (carbs, protein, fat) == (
        totals["Carbohydrates (g)"],
        totals["Protein (g)"],
        totals["Fat (g)"],
    )
    for card in view.meals:
        assert min(number(card.carbs), number(card.protein), number(card.fat)) >= 0
        assert number(card.kcal) == 4 * number(card.carbs) + 4 * number(card.protein) + 9 * number(
            card.fat
        )
    assert totals["Energy target (kcal)"] == (
        totals["Energy spent (kcal)"] - totals["Calorie deficit (kcal)"]
    )
    assert totals["Energy target (kcal)"] >= totals["Basal metabolism (kcal)"]


@pytest.mark.parametrize("person", PEOPLE, ids=lambda p: f"{p.sex.value}-{p.age_years}")
@pytest.mark.parametrize(
    "goal",
    [MaintenanceGoal(), WeightLossGoal(100), WeightLossGoal(1000)],
    ids=lambda g: type(g).__name__,
)
def test_t_ui_01_every_advisory_of_the_model_is_worded_on_the_page(
    person: Person, goal: MaintenanceGoal | WeightLossGoal
) -> None:
    report = report_for(person, goal, (run(7 * 60, 200.0, 2000.0, 250.0),))

    view = present_day(DAY, report)

    assert isinstance(view, DayView)
    assert view.advisories == tuple(advisory_text(a) for a in report.nutrition.advisories)
    assert all(text.strip() for text in view.advisories)
    assert view.notice.strip()  # the fixed notice is always there


def test_t_ui_01_a_minor_and_a_thin_person_are_told_to_talk_to_a_professional() -> None:
    teen = next(p for p in PEOPLE if p.age_years == 17)
    thin = next(p for p in PEOPLE if p.body_mass_index < 18.5 and p.age_years > 17)

    for person in (teen, thin):
        view = present_day(DAY, report_for(person, WeightLossGoal(1000)))
        assert isinstance(view, DayView)
        text = " ".join(view.advisories).lower()
        assert "doctor or a dietitian" in text
        assert "does not suggest a calorie deficit" in text or "body mass index" in text
        assert view.totals[2][1] == "0"  # the calorie deficit row


def test_t_ui_01_a_day_that_cannot_be_planned_says_what_is_missing_for_each_reason() -> None:
    for reason in UnavailableReason:
        view = present_day(DAY, NutritionUnavailable(reason))

        assert isinstance(view, UnavailableView)
        assert view.missing == unavailable_text(reason)
        assert view.missing.what.strip() and view.missing.remedy.strip() and view.notice.strip()


# --- The month grid agrees with what it is built from -----------------------------------------

SPORTS = list(Sport)
PERIOD_SETS = [
    [],
    [GoalPeriod(date(2026, 6, 8), date(2026, 6, 14), WeightLossGoal(500))],
    [
        GoalPeriod(date(2026, 5, 1), date(2026, 5, 31), PerformanceGoal()),
        GoalPeriod(date(2026, 6, 1), None, WeightLossGoal(250)),
    ],
    [GoalPeriod(date(2026, 6, 30), date(2026, 6, 30), MaintenanceGoal())],
]


@settings(deadline=None, max_examples=150)
@given(
    month=st.integers(0, 47),
    today_offset=st.integers(-40, 40),
    picks=st.lists(
        st.tuples(
            st.integers(-10, 40), st.integers(0, 23), st.sampled_from(SPORTS), st.integers(0, 400)
        ),
        max_size=25,
    ),
    periods=st.sampled_from(PERIOD_SETS),
)
@pytest.mark.collaudo
def test_t_ui_01_the_month_grid_holds_every_activity_once_and_every_rule(
    month: int,
    today_offset: int,
    picks: list[tuple[int, int, Sport, int]],
    periods: list[GoalPeriod],
) -> None:
    first = first_of_month(date(2024, 1, 1))
    first = shift_month(first, month)  # January 2024 to December 2027: a leap year included
    today = first + timedelta(days=today_offset)
    activities = [
        profile_activity(
            datetime.combine(first + timedelta(days=offset), datetime.min.time(), UTC)
            + timedelta(hours=hour),
            sport=sport,
            load=float(load),
            serial=i + 1,
        )
        for i, (offset, hour, sport, load) in enumerate(picks)
    ]

    view = build_month(first, today, activities, periods)

    cells = view.days
    assert len(cells) % 7 == 0 and 28 <= len(cells) <= 42
    assert cells[0].day.weekday() == 0 and cells[-1].day.weekday() == 6
    assert {b.day - a.day for a, b in pairwise(cells)} == {timedelta(days=1)}
    in_month = [c for c in cells if c.in_month]
    assert in_month[0].day == first
    assert all(c.day.month == first.month for c in in_month)
    in_this_month = [
        a for a in activities if a.local_day.month == first.month and a.local_day.year == first.year
    ]
    assert sum(c.activity_count for c in cells) == len(in_this_month)
    for cell in cells:
        mine = [a for a in in_this_month if a.local_day == cell.day]
        assert cell.activity_count == len(mine)
        assert cell.load == pytest.approx(sum(a.load.trimp_eq for a in mine))
        assert len(set(cell.sports)) == len(cell.sports)
        assert cell.is_today is (cell.day == today)
        assert cell.is_future is (cell.day > today)
        assert cell.can_open is (cell.in_month and cell.day <= today)
        if not cell.in_month:
            assert (cell.activity_count, cell.load, cell.goal) == (0, 0.0, None)
        else:
            covering = [p for p in periods if p.contains(cell.day)]
            assert (cell.goal is None) is (not covering)
            if covering:
                assert cell.goal is goal_kind_of(goal_for_day(periods, cell.day))


def test_t_ui_01_the_edges_of_a_goal_period_are_inclusive_on_the_calendar() -> None:
    periods = [GoalPeriod(date(2026, 6, 8), date(2026, 6, 14), WeightLossGoal(500))]
    cells = {c.day: c for c in build_month(date(2026, 6, 1), date(2026, 6, 30), [], periods).days}

    assert cells[date(2026, 6, 7)].goal is None
    assert cells[date(2026, 6, 8)].goal is GoalKind.WEIGHT_LOSS
    assert cells[date(2026, 6, 14)].goal is GoalKind.WEIGHT_LOSS
    assert cells[date(2026, 6, 15)].goal is None


def test_t_ui_01_leap_day_and_the_turn_of_the_year_are_cells_like_the_others() -> None:
    february = build_month(date(2028, 2, 1), date(2028, 3, 1), [], [])
    december = build_month(date(2026, 12, 1), date(2027, 1, 1), [], [])

    assert sum(c.in_month for c in february.days) == 29
    assert february.cell_of(date(2028, 2, 29)) is not None
    assert december.weeks[-1][-1].day == date(2027, 1, 3)  # the week ends on Sunday
    assert not december.weeks[-1][-1].in_month
    assert shift_month(date(2026, 12, 1), 1) == date(2027, 1, 1)
    assert shift_month(date(2027, 1, 1), -1) == date(2026, 12, 1)
    assert shift_month(date(1900, 1, 1), -1) == date(1900, 1, 1)  # never before the earliest day


# --- Limits of the settings -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "problems"),
    [
        ((450, 570, 690), set()),  # exactly two hours apart
        ((450, 569, 690), {MealField.LUNCH}),
        ((450, 570, 689), {MealField.DINNER}),
        ((450, 451, 452), {MealField.LUNCH, MealField.DINNER}),
        ((0, 120, 240), set()),
        ((1199, 1319, 1439), set()),
        ((700, 600, 500), {MealField.LUNCH, MealField.DINNER}),  # out of order
        ((600, 600, 600), {MealField.LUNCH, MealField.DINNER}),
    ],
)
def test_t_ui_01_the_two_hour_rule_of_the_meal_times_at_its_edges(
    minutes: tuple[int, int, int], problems: set[MealField]
) -> None:
    assert meal_time_problems(minutes) == problems
    assert (meal_times_of(minutes) is None) is bool(problems)


def test_t_ui_01_a_period_is_refused_for_its_dates_and_adjacent_ones_are_not() -> None:
    saved = [
        GoalPeriod(date(2026, 6, 8), date(2026, 6, 14), WeightLossGoal(500)),
        GoalPeriod(date(2026, 7, 1), None, MaintenanceGoal()),
    ]

    assert period_issue(date(2026, 6, 15), date(2026, 6, 30), saved) is None
    assert period_issue(date(2026, 6, 14), date(2026, 6, 20), saved) is not None
    overlap = period_issue(date(2026, 6, 1), date(2026, 6, 8), saved)
    assert overlap is not None and overlap.problem is PeriodProblem.OVERLAPS
    assert overlap.other == saved[0]
    assert period_issue(date(2026, 8, 1), None, saved) is not None  # inside the open-ended one
    assert period_issue(date(2026, 5, 1), None, saved) is not None  # runs into the first
    assert period_issue(date(2026, 6, 20), date(2026, 6, 19), saved) is not None  # ends first
    assert period_issue(date(2026, 6, 8), date(2026, 6, 14), saved, date(2026, 6, 8)) is None


@pytest.mark.parametrize(
    ("kilograms", "grams"),
    [(0.0, 100), (-3.0, 100), (0.099, 100), (0.1, 100), (0.5, 500), (1.0, 1000), (7.5, 1000)],
)
def test_t_ui_01_the_weight_loss_pace_is_kept_inside_the_limits_of_the_model(
    kilograms: float, grams: int
) -> None:
    assert pace_in_grams(kilograms) == grams
    goal = goal_of(GoalKind.WEIGHT_LOSS, grams * 100)  # whatever the pace asked, it is limited
    assert isinstance(goal, WeightLossGoal) and 100 <= goal.weekly_loss_g <= 1000
    assert pace_of(goal) == goal.weekly_loss_g
    assert pace_of(MaintenanceGoal()) == 500


# --- T-UI-07: the choices survive a restart of the application -------------------------------


def test_t_ui_07_level_meal_times_and_goal_survive_a_restart_and_drive_the_next_day(
    locale_restored: None, qtbot: QtBot, tmp_path: Path
) -> None:
    QLocale.setDefault(LOCALES[0])
    settings_file = make_settings(tmp_path)
    window = window_with_days(qtbot, tmp_path, settings_file)
    dialog = open_settings(qtbot, window)
    with qtbot.waitSignal(window.nutrition.loader.settings_changed, timeout=WAIT_MS):
        dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.VERY_ACTIVE))
        dialog.level_from_edit.setDate(QDate(2026, 6, 1))
        dialog.set_level_button.click()
    dialog.time_edits[MealField.BREAKFAST].setTime(QTime(6, 15))
    with qtbot.waitSignal(window.nutrition.loader.settings_changed, timeout=WAIT_MS):
        window.settings_window.save_button.click()
    dialog.add_period()
    sub = dialog.period_dialog
    assert sub is not None
    sub.set_dates(date(2026, 6, 8), date(2026, 6, 14))
    with qtbot.waitSignal(window.nutrition.loader.settings_changed, timeout=WAIT_MS):
        sub.save_button.click()
    dialog.close()
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    before = (
        last_day_page(window).totals_model.rows,
        last_day_page(window).meal_titles,
        last_day_page(window).summary_line,
    )
    close_window(qtbot, window)

    second = compose(tmp_path, settings_file)
    qtbot.addWidget(second)
    second.start()
    qtbot.waitUntil(lambda: second.controller.profile is not None, timeout=WAIT_MS)
    wait_ready(qtbot, second, 3)
    open_day(qtbot, second, DAWN_AND_LUNCH_DAY)

    after = (
        last_day_page(second).totals_model.rows,
        last_day_page(second).meal_titles,
        last_day_page(second).summary_line,
    )
    assert after == before
    assert "Weight loss" in last_day_page(second).summary_line
    open_day(qtbot, second, EMPTY_DAY)
    assert (
        advisory_text(Advisory.ACTIVITY_LEVEL_ASSUMED) not in last_day_page(second).advisory_texts
    )
    assert plain(last_day_page(second).meal_titles[0]) == "Breakfast at 6:15 AM"
