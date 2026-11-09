# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The menu of a calendar day: goals and injuries are added and followed from the days.

From the Composition Root with the made-up profile of `nutrition_support` (today is 30 June 2026,
the athlete "Atleta di prova"): the right button or the Menu key opens the menu of the day, a goal
starts on the selected day, an injury or an illness begins on it, and the commands on a goal or an
injury act on the record that falls on the selected day.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QPoint, QSettings
from PySide6.QtGui import QAction, QContextMenuEvent
from PySide6.QtWidgets import QApplication, QDateEdit, QMenu
from pytestqt.qtbot import QtBot

from ponos_coach.ui.birth_date_edit import from_qdate
from ponos_coach.ui.main_window import MainWindow
from tests.ui.goals_support import RACE_DAY, goals_of, wait_for
from tests.ui.injury_support import injury_window, report_injury, select_record, summary_of
from tests.ui.nutrition_support import cell_of
from tests.ui.workspace_support import TODAY, make_settings

PAST_DAY = TODAY - timedelta(days=3)
FUTURE_DAY = TODAY + timedelta(days=10)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def shown_window(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    """The window of `injury_window` (a race and its plan), shown on the Calendar."""
    window = injury_window(qtbot, tmp_path, settings)
    window.show()
    qtbot.waitExposed(window)
    window.sidebar.setCurrentRow(2)
    return window


def texts_of(menu: QMenu) -> list[str]:
    return [a.text() for a in menu.actions() if not a.isSeparator()]


def action_named(menu: QMenu, text: str) -> QAction:
    return next(a for a in menu.actions() if a.text() == text)


def right_click(window: MainWindow, day: date) -> None:
    """The right button on the cell of a day (the month shown holds it)."""
    table = window.calendar_page.table
    index = window.calendar_page.model.index_of(day)
    position = table.visualRect(index).center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, position, table.viewport().mapToGlobal(position)
    )
    QApplication.sendEvent(table.viewport(), event)


def close_menu(window: MainWindow) -> None:
    menu = window.day_menu.menu
    assert menu is not None
    menu.close()


# --- Opening the menu ---------------------------------------------------------------------------


def test_the_right_button_selects_the_day_and_opens_its_menu_with_the_commands_and_reasons(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)

    right_click(window, PAST_DAY)

    assert window.calendar_page.selected_day() == PAST_DAY
    menu = window.day_menu.menu
    assert menu is not None
    assert texts_of(menu) == [
        "Open day",
        "Add race...",
        "Add form goal...",
        "Add injury...",
        "Add illness...",
    ]
    add_race = action_named(menu, "Add race...")
    assert not add_race.isEnabled()
    assert add_race.toolTip() == "A goal needs a day after today."
    assert action_named(menu, "Add injury...").isEnabled()
    assert menu.toolTipsVisible()
    close_menu(window)


def test_the_menu_key_opens_the_menu_of_the_selected_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    page = window.calendar_page
    page.select_day(PAST_DAY)
    table = page.table
    centre = table.viewport().mapToGlobal(table.visualRect(table.currentIndex()).center())

    QApplication.sendEvent(
        table, QContextMenuEvent(QContextMenuEvent.Reason.Keyboard, QPoint(0, 0), centre)
    )

    menu = window.day_menu.menu
    assert menu is not None
    assert "Add injury..." in texts_of(menu)
    close_menu(window)


def test_a_day_with_a_goal_or_an_injury_offers_the_commands_on_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    page = window.calendar_page

    page.go_to(RACE_DAY)
    with_goal = window.day_menu.build(cell_of(window, RACE_DAY))
    page.go_to(TODAY)
    wait_for(qtbot, lambda: bool(cell_of(window, TODAY).problems))
    with_injury = window.day_menu.build(cell_of(window, TODAY))
    page.go_to(FUTURE_DAY)
    plain = window.day_menu.build(cell_of(window, FUTURE_DAY))

    assert "Change goal..." in texts_of(with_goal)
    assert "Remove goal" in texts_of(with_goal)
    assert "Daily check-in..." not in texts_of(with_goal)
    assert texts_of(with_injury)[-3:] == [
        "Daily check-in...",
        "Medical review done",
        "Close injury or illness...",
    ]
    assert "Change goal..." not in texts_of(plain)
    assert "Close injury or illness..." not in texts_of(plain)


# --- Goals ------------------------------------------------------------------------------------


def test_a_goal_added_from_a_day_starts_on_that_day_whether_from_the_menu_or_the_shortcut(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    page = window.calendar_page
    page.go_to(FUTURE_DAY)
    assert window.plan.actions["add_race"].isEnabled()

    action_named(window.day_menu.build(cell_of(window, FUTURE_DAY)), "Add race...").trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.draft().day == FUTURE_DAY
    dialog.reject()
    wait_for(qtbot, lambda: window.plan.goal_dialog is None)

    window.plan.actions["add_form_goal"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.draft().day == FUTURE_DAY
    dialog.reject()


def test_a_goal_cannot_start_on_a_day_that_is_not_after_today_and_the_tooltip_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    window.calendar_page.go_to(PAST_DAY)

    for name in ("add_race", "add_form_goal"):
        action = window.plan.actions[name]
        assert not action.isEnabled()
        assert action.toolTip() == "A goal needs a day after today."


def test_the_goal_of_the_selected_day_is_removed_from_the_menu_and_the_removal_is_undone(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    page = window.calendar_page
    page.go_to(RACE_DAY)
    (race,) = goals_of(window)
    menu = window.day_menu.build(cell_of(window, RACE_DAY))

    action_named(menu, "Remove goal").trigger()
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == ())
    wait_for(qtbot, lambda: not cell_of(window, RACE_DAY).goals)

    assert not window.plan.actions["remove_goal"].isEnabled()
    window.plan.actions["undo"].trigger()
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == (race,))


def test_two_goals_on_one_day_make_the_command_ask_which(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    window.plan.add_form_goal(RACE_DAY)
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and len(goals_of(window)) == 2)
    window.calendar_page.go_to(RACE_DAY)
    wait_for(qtbot, lambda: len(cell_of(window, RACE_DAY).goals) == 2)

    window.plan.actions["change_goal"].trigger()

    menu = window.findChild(QMenu, "choiceMenu")
    assert menu is not None
    assert len(menu.actions()) == 2
    assert any(text.startswith("Form goal") for text in texts_of(menu))
    menu.actions()[0].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.reject()


# --- Injuries ---------------------------------------------------------------------------------


def test_an_injury_added_from_a_day_begins_on_that_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    window.calendar_page.go_to(PAST_DAY)

    action_named(window.day_menu.build(cell_of(window, PAST_DAY)), "Add injury...").trigger()

    wait_for(qtbot, lambda: window.injuries.wizard is not None)
    wizard = window.injuries.wizard
    assert wizard is not None
    edit = wizard.where_page.findChild(QDateEdit, "injuryOnsetDay")
    assert edit is not None
    assert from_qdate(edit.date()) == PAST_DAY
    wizard.reject()


def test_an_illness_added_from_a_day_is_felt_since_that_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    window.calendar_page.go_to(PAST_DAY)

    window.injuries.actions["report_illness"].trigger()

    wait_for(qtbot, lambda: window.injuries.illness_dialog is not None)
    dialog = window.injuries.illness_dialog
    assert dialog is not None
    assert from_qdate(dialog.date_edit.date()) == PAST_DAY
    dialog.reject()


def test_an_injury_or_an_illness_cannot_begin_after_today_and_the_tooltip_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)

    window.calendar_page.go_to(FUTURE_DAY)

    actions = window.injuries.actions
    assert not actions["report_injury"].isEnabled()
    assert actions["report_injury"].toolTip() == "An injury cannot begin after today."
    assert not actions["report_illness"].isEnabled()
    assert actions["report_illness"].toolTip() == "An illness cannot begin after today."


def test_the_commands_on_an_injury_act_on_the_record_of_the_selected_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = shown_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    window.calendar_page.go_to(FUTURE_DAY)
    check_in = window.injuries.actions["check_in"]
    assert not check_in.isEnabled()

    select_record(qtbot, window)
    assert check_in.isEnabled()
    menu = window.day_menu.build(cell_of(window, summary_of(window).started_on))
    action_named(menu, "Close injury or illness...").trigger()

    wait_for(qtbot, lambda: window.injuries.is_idle and not summary_of(window).is_open)
    assert not window.injuries.actions["close"].isEnabled()
    window.injuries.actions["undo"].trigger()
    wait_for(qtbot, lambda: window.injuries.is_idle and summary_of(window).is_open)
