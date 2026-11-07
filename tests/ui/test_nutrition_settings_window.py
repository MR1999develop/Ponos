# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The "Nutrition settings" dialog from the Composition Root: activity level, meal times and goal
periods, with undo, and the calendar and the day page following the changes.

Made-up profile and activities (see `nutrition_support`).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QLocale, QSettings, Qt, QTime
from PySide6.QtWidgets import QComboBox, QDateEdit, QTimeEdit
from pytestqt.qtbot import QtBot
from pytestqt.wait_signal import SignalBlocker

from ponos_coach.nutrition.domain import ActivityLevel, Advisory, GoalPeriod, MaintenanceGoal
from ponos_coach.ui.calendar_model import GoalKind
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_settings_dialog import NutritionSettingsDialog
from ponos_coach.ui.nutrition_settings_model import MealField
from ponos_coach.ui.nutrition_texts import advisory_text
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    EMPTY_DAY,
    cell_of,
    open_day,
    open_settings,
    plain,
    short_date,
    window_with_days,
)
from tests.ui.workspace_support import WAIT_MS, make_settings

FIRST = date(2026, 6, 8)
LAST = date(2026, 6, 14)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def changed(qtbot: QtBot, window: MainWindow) -> SignalBlocker:
    """Wait for the next stored change (use with `with`)."""
    return qtbot.waitSignal(window.nutrition.loader.settings_changed, timeout=WAIT_MS)


def add_period(
    qtbot: QtBot,
    window: MainWindow,
    dialog: NutritionSettingsDialog,
    first: date = FIRST,
    last: date | None = LAST,
) -> None:
    """Add a weight loss period of 0.5 kg per week through the sub-dialog."""
    dialog.add_period()
    sub = dialog.period_dialog
    assert sub is not None
    sub.set_dates(first, last)
    assert sub.save_button.isEnabled()
    with changed(qtbot, window):
        sub.save_button.click()


def test_the_settings_open_from_the_profile_menu_with_the_defaults_and_nothing_to_undo(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    dialog = open_settings(qtbot, window)

    assert window.nutrition_action.shortcut().toString() == "Ctrl+Shift+N"
    assert dialog.windowTitle() == "Nutrition settings"
    assert dialog.levels_model.rowCount() == 0
    assert dialog.periods_model.rowCount() == 0
    assert [e.time() for e in dialog.time_edits.values()] == [
        QTime(7, 30),
        QTime(13, 0),
        QTime(20, 0),
    ]  # the defaults
    assert not dialog.undo_button.isEnabled()
    assert not dialog.edit_period_button.isEnabled()
    assert dialog.edit_period_button.toolTip() == "Select a period in the table first."


def test_every_field_of_the_dialog_has_an_accessible_name(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    fields = [
        *dialog.findChildren(QComboBox),
        *dialog.findChildren(QDateEdit),
        *dialog.findChildren(QTimeEdit),
    ]

    assert fields
    assert all(field.accessibleName() for field in fields)
    assert dialog.periods_table.model().headerData(3, Qt.Orientation.Horizontal) == (
        "Weight loss pace (kg per week)"
    )


def test_setting_the_activity_level_from_a_day_is_saved_listed_and_used_by_the_day(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.ACTIVE))
    dialog.level_from_edit.setDate(QDate(2026, 6, 1))
    with changed(qtbot, window):
        dialog.set_level_button.click()

    assert dialog.levels_model.rows == ((short_date(date(2026, 6, 1)), "Active"),)
    open_day(qtbot, window, EMPTY_DAY)
    assert advisory_text(Advisory.ACTIVITY_LEVEL_ASSUMED) not in window.day_page.advisory_texts


def test_the_activity_level_cannot_hold_from_a_day_that_has_not_come(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    dialog.level_from_edit.setDate(QDate(2026, 7, 15))

    assert dialog.level_from_edit.date() == QDate(2026, 6, 30)  # clamped to today


def test_meal_times_less_than_two_hours_apart_are_refused_with_a_message_on_the_field(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    dialog.time_edits[MealField.LUNCH].setTime(QTime(8, 30))

    assert dialog.time_error(MealField.LUNCH) == "Lunch must be at least 2 hours after breakfast."
    assert dialog.time_error(MealField.DINNER) == ""
    assert not dialog.save_times_button.isEnabled()
    assert dialog.save_times_button.toolTip() == "Solve the problem with the times first."
    dialog.time_edits[MealField.LUNCH].setTime(QTime(13, 0))
    dialog.time_edits[MealField.DINNER].setTime(QTime(14, 30))
    assert dialog.time_error(MealField.DINNER) == "Dinner must be at least 2 hours after lunch."
    assert dialog.time_error(MealField.LUNCH) == ""


def test_saved_meal_times_move_the_meals_of_the_day_and_undo_brings_them_back(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    dialog.time_edits[MealField.DINNER].setTime(QTime(21, 0))
    assert dialog.save_times_button.isEnabled()
    with changed(qtbot, window):
        dialog.save_times_button.click()
    open_day(qtbot, window, EMPTY_DAY)
    assert [plain(t.split(" at ")[1]) for t in window.day_page.meal_titles][-1] == "9:00 PM"

    assert dialog.undo_stack.count() == 1
    assert dialog.undo_stack.undoText() == "Change meal times"
    with changed(qtbot, window):
        dialog.undo_stack.undo()
    assert dialog.time_edits[MealField.DINNER].time() == QTime(20, 0)
    qtbot.waitUntil(
        lambda: [plain(t.split(" at ")[1]) for t in window.day_page.meal_titles][-1] == "8:00 PM",
        timeout=WAIT_MS,
    )


def test_a_goal_period_is_added_shown_in_the_table_and_the_calendar_and_undone(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    add_period(qtbot, window, dialog)

    assert dialog.periods_model.rows == (
        (short_date(FIRST), short_date(LAST), "Weight loss", "0.50"),
    )
    assert cell_of(window, date(2026, 6, 10)).goal is GoalKind.WEIGHT_LOSS
    assert cell_of(window, date(2026, 6, 15)).goal is None
    assert "Weight loss" in str(
        window.calendar_page.model.data(window.calendar_page.model.index_of(date(2026, 6, 10)))
    )
    assert dialog.undo_stack.undoText() == "Add goal period"
    with changed(qtbot, window):
        dialog.undo_stack.undo()
    assert dialog.periods_model.rowCount() == 0
    assert cell_of(window, date(2026, 6, 10)).goal is None
    with changed(qtbot, window):
        dialog.undo_stack.redo()
    assert dialog.periods_model.rowCount() == 1
    assert cell_of(window, date(2026, 6, 10)).goal is GoalKind.WEIGHT_LOSS


def test_a_period_that_overlaps_another_is_refused_with_a_clear_message_and_nothing_is_saved(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    add_period(qtbot, window, dialog)

    dialog.add_period()
    sub = dialog.period_dialog
    assert sub is not None
    sub.set_dates(date(2026, 6, 12), date(2026, 6, 20))

    assert sub.error_text == (
        f"These days overlap the period from {short_date(FIRST)} to {short_date(LAST)}."
    )
    assert not sub.save_button.isEnabled()
    sub.set_dates(date(2026, 6, 20), date(2026, 6, 12))
    assert sub.error_text == "The last day cannot be before the first day."
    sub.set_dates(date(2026, 6, 15), None)
    assert sub.error_text == ""
    assert sub.save_button.isEnabled()
    sub.reject()

    assert dialog.periods_model.rowCount() == 1
    assert dialog.undo_stack.count() == 1


def test_a_period_is_changed_through_the_sub_dialog_and_the_open_day_follows(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    add_period(qtbot, window, dialog)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    qtbot.waitUntil(
        lambda: window.day_page.summary_line.startswith("Goal: Weight loss, 0.50 kg per week"),
        timeout=WAIT_MS,
    )

    dialog.select_period(FIRST)
    assert dialog.edit_period_button.isEnabled()
    dialog.edit_period()
    sub = dialog.period_dialog
    assert sub is not None
    assert sub.first_day_edit.date() == QDate(2026, 6, 8)
    sub.set_dates(date(2026, 6, 9), None)  # no end date; the period does not clash with itself
    sub.goal_combo.setCurrentIndex(sub.goal_combo.findData(GoalKind.PERFORMANCE))
    assert not sub.pace_spin.isEnabled()  # a pace is only for weight loss
    with changed(qtbot, window):
        sub.save_button.click()

    assert dialog.periods_model.rows == (
        (short_date(date(2026, 6, 9)), "No end date", "Performance", ""),
    )
    assert dialog.undo_stack.undoText() == "Change goal period"
    qtbot.waitUntil(
        lambda: window.day_page.summary_line.startswith("Goal: Performance"), timeout=WAIT_MS
    )
    assert cell_of(window, date(2026, 6, 8)).goal is None
    assert cell_of(window, date(2026, 6, 30)).goal is GoalKind.PERFORMANCE
    with changed(qtbot, window):
        dialog.undo_stack.undo()
    assert dialog.periods_model.rows == (
        (short_date(FIRST), short_date(LAST), "Weight loss", "0.50"),
    )


def test_removing_a_period_is_undoable_and_the_weight_loss_pace_stays_in_the_domain_limits(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    dialog.add_period()
    sub = dialog.period_dialog
    assert sub is not None
    sub.pace_spin.setValue(5.0)  # above the limit: the control clamps it
    assert sub.pace_spin.value() == pytest.approx(1.0)
    sub.pace_spin.setValue(0.01)
    assert sub.pace_spin.value() == pytest.approx(0.1)
    sub.reject()

    add_period(qtbot, window, dialog)
    dialog.select_period(FIRST)
    with changed(qtbot, window):
        dialog.remove_period()
    assert dialog.periods_model.rowCount() == 0
    assert cell_of(window, date(2026, 6, 10)).goal is None
    with changed(qtbot, window):
        dialog.undo_stack.undo()

    assert dialog.periods_model.rowCount() == 1
    assert dialog.undo_button.isEnabled()


def test_the_periods_survive_closing_and_reopening_the_dialog(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    add_period(qtbot, window, dialog)
    dialog.close()
    qtbot.waitUntil(lambda: window.nutrition.settings_dialog is None, timeout=WAIT_MS)

    reopened = open_settings(qtbot, window)

    assert reopened.periods_model.rowCount() == 1
    assert reopened.undo_stack.count() == 0  # a new dialog starts with a new history


def test_a_change_the_store_refuses_is_reported_in_the_dialog_and_the_history_is_cleared(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    add_period(qtbot, window, dialog)
    clashing = GoalPeriod(date(2026, 6, 10), date(2026, 6, 11), MaintenanceGoal())

    with qtbot.waitSignal(window.nutrition.loader.command_failed, timeout=WAIT_MS):
        window.nutrition.loader.add_period(clashing)  # as if another change came first

    qtbot.waitUntil(lambda: dialog.failure_text != "", timeout=WAIT_MS)
    assert dialog.failure_text.startswith("The goal periods overlap.")
    assert "Choose dates that no other period uses." in dialog.failure_text
    assert dialog.undo_stack.count() == 0  # it no longer matches what is stored
    assert dialog.periods_model.rowCount() == 1  # the table shows what is really stored


def test_setting_the_activity_level_is_undoable_and_redoable_with_a_description(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    first = date(2026, 6, 1)
    dialog.level_from_edit.setDate(QDate(first.year, first.month, first.day))
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.LIGHT))
    with changed(qtbot, window):
        dialog.set_level_button.click()
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.VERY_ACTIVE))
    with changed(qtbot, window):
        dialog.set_level_button.click()

    assert dialog.undo_stack.count() == 2
    assert dialog.undo_stack.undoText() == "Set activity level"
    assert dialog.levels_model.rows == ((short_date(first), "Very active"),)
    with changed(qtbot, window):
        dialog.undo_stack.undo()
    assert dialog.levels_model.rows == ((short_date(first), "Lightly active"),)
    with changed(qtbot, window):
        dialog.undo_stack.redo()
    assert dialog.levels_model.rows == ((short_date(first), "Very active"),)


def test_undoing_the_first_level_of_a_day_removes_the_entry_and_the_assumed_notice_returns(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.ACTIVE))
    dialog.level_from_edit.setDate(QDate(2026, 6, 1))
    with changed(qtbot, window):
        dialog.set_level_button.click()

    with changed(qtbot, window):
        dialog.undo_stack.undo()

    assert dialog.levels_model.rowCount() == 0  # the store is as before, not a "sedentary" row
    open_day(qtbot, window, EMPTY_DAY)
    assert advisory_text(Advisory.ACTIVITY_LEVEL_ASSUMED) in window.day_page.advisory_texts
    with changed(qtbot, window):
        dialog.undo_stack.redo()
    assert dialog.levels_model.rows == ((short_date(date(2026, 6, 1)), "Active"),)


def test_two_quick_level_changes_followed_by_two_undos_leave_the_store_as_it_was(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    dialog.level_from_edit.setDate(QDate(2026, 6, 1))
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.LIGHT))
    dialog.set_level_button.click()  # no wait: the stored settings are still the old ones
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.VERY_ACTIVE))
    dialog.set_level_button.click()
    expected = ((short_date(date(2026, 6, 1)), "Very active"),)
    qtbot.waitUntil(lambda: dialog.levels_model.rows == expected, timeout=WAIT_MS)

    dialog.undo_stack.undo()
    dialog.undo_stack.undo()  # also without waiting: the jobs run in order

    qtbot.waitUntil(lambda: dialog.levels_model.rowCount() == 0, timeout=WAIT_MS)
    qtbot.wait(100)  # a late failure would clear the stack and show a message
    assert dialog.failure_text == ""
    assert dialog.undo_stack.count() == 2
    assert dialog.levels_model.rowCount() == 0
