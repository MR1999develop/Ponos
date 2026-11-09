# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The page of a day ahead, from the Composition Root: an estimate from the saved plan, the note
that says so, no deficit in the taper before an A race, and what is shown without a plan
(T-NUT-07, T-NUT-08). Made-up profile; the tests' today is 30 June 2026, the plan's race 14 weeks
later."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.nutrition.domain import Advisory, EnergySource, GoalPeriod, WeightLossGoal
from ponos_coach.nutrition.use_cases import AddGoalPeriod
from ponos_coach.planning.domain import Phase, Plan, build_plan
from ponos_coach.profiles.day_nutrition import UnavailableReason
from ponos_coach.ui.data_thread import JobSucceeded
from ponos_coach.ui.day_page import DayState
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_texts import (
    advisory_text,
    energy_source_text,
    estimate_from_plan_text,
    unavailable_text,
)
from ponos_coach.ui.workspace import ProfileAccess
from tests.planning.builders import BIG_WEEK, inputs, race
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    last_day_page,
    open_day,
    window_with_days,
)
from tests.ui.test_plan_after_sync import run_on_data_thread
from tests.ui.workspace_support import TODAY, WAIT_MS, make_settings

RACE_DAY = TODAY + timedelta(weeks=14)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def save_plan_and_weight_loss(qtbot: QtBot, window: MainWindow) -> Plan:
    """Save the plan of the real engine (an A race in 14 weeks) and a weight-loss period."""
    built = build_plan(inputs([race("a", RACE_DAY)], avail=BIG_WEEK), TODAY)
    assert isinstance(built, Plan)

    def work(access: ProfileAccess) -> None:
        access.session.plans.replace_current(built, TODAY)
        period = GoalPeriod(date(2026, 1, 1), None, WeightLossGoal(500))
        AddGoalPeriod(access.session.activities, access.session.goal_periods)(period)

    assert isinstance(run_on_data_thread(qtbot, window, work), JobSucceeded)
    return built


def show_day(qtbot: QtBot, window: MainWindow, day: date) -> None:
    window.nutrition.open_day(day)
    qtbot.waitUntil(
        lambda: last_day_page(window).state in (DayState.CONTENT, DayState.UNAVAILABLE),
        timeout=WAIT_MS,
    )


def test_a_day_ahead_lists_the_planned_session_and_says_it_is_an_estimate_from_the_plan(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    plan = save_plan_and_weight_loss(qtbot, window)
    target = next(d for d in plan.days if d.session is not None and d.phase is Phase.BASE)
    assert target.session is not None

    show_day(qtbot, window, target.day)
    page = last_day_page(window)

    assert page.state is DayState.CONTENT
    assert page.estimate_text == estimate_from_plan_text()
    assert page.estimate_text == "Estimate from the plan: the activities are the planned sessions."
    (row,) = page.activities_model.rows
    assert row[5] == energy_source_text(EnergySource.MET)
    assert page.in_progress_text == ""
    assert advisory_text(Advisory.DEFICIT_PAUSED_FOR_RACE) not in page.advisory_texts


def test_in_the_taper_before_the_race_the_page_says_that_there_is_no_deficit(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    plan = save_plan_and_weight_loss(qtbot, window)
    taper = next(d for d in plan.days if d.phase is Phase.TAPER)

    show_day(qtbot, window, taper.day)

    assert advisory_text(Advisory.DEFICIT_PAUSED_FOR_RACE) in last_day_page(window).advisory_texts


def test_a_day_ahead_without_a_plan_says_that_the_plan_does_not_cover_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    show_day(qtbot, window, TODAY + timedelta(days=1))
    page = last_day_page(window)

    assert page.state is DayState.UNAVAILABLE
    missing = unavailable_text(UnavailableReason.NO_PLAN_FOR_DAY)
    assert page.missing_texts == (missing.title, missing.what, missing.remedy)
    assert missing.title == "The plan does not cover this day"
    assert not page.edit_profile_button.isVisibleTo(window)


def test_a_past_day_has_no_estimate_note_even_with_a_plan(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    save_plan_and_weight_loss(qtbot, window)

    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    assert last_day_page(window).estimate_text == ""
