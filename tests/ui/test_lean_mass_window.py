# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The measured lean mass in "Nutrition settings" and on the day page, from the Composition Root.

The profile is made up (a weight of 65.5 kg, no real person). The tests' today is 30 June 2026.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QLocale, QSettings
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot
from pytestqt.wait_signal import SignalBlocker

from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_settings_dialog import NutritionSettingsDialog
from tests.ui.nutrition_support import (
    DAWN_AND_LUNCH_DAY,
    last_day_page,
    open_day,
    open_settings,
    short_date,
    window_with_days,
)
from tests.ui.workspace_support import WAIT_MS, make_settings

FIRST_DAY = date(2026, 6, 1)


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


def set_lean_mass(
    qtbot: QtBot, window: MainWindow, dialog: NutritionSettingsDialog, text: str
) -> None:
    """Type a lean mass valid from 1 June and set it; wait until it is saved."""
    section = dialog.lean_mass_section
    section.value_edit.setText(text)
    section.from_edit.setDate(QDate(FIRST_DAY.year, FIRST_DAY.month, FIRST_DAY.day))
    with changed(qtbot, window):
        section.set_button.click()


def test_the_lean_mass_starts_empty_with_nothing_selected(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)

    section = open_settings(qtbot, window).lean_mass_section

    assert section.title() == "Measured lean mass"
    assert section.model.rowCount() == 0
    assert not section.remove_button.isEnabled()
    assert section.remove_button.toolTip() == "Select a measurement in the list first."
    assert section.value_edit.accessibleName() == "Lean mass (kg)"


def test_setting_the_lean_mass_lists_it_and_is_one_undoable_step(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)

    set_lean_mass(qtbot, window, dialog, "50,5")  # a comma works like a point

    section = dialog.lean_mass_section
    assert section.model.rows == ((short_date(FIRST_DAY), "50.5"),)
    assert section.problem_text == ""
    assert dialog.undo_stack.count() == 1
    assert dialog.undo_stack.undoText() == "Set lean mass"
    assert dialog.undo_button.isEnabled()

    with changed(qtbot, window):
        dialog.undo_button.click()
    assert section.model.rowCount() == 0

    with changed(qtbot, window):
        dialog.redo_button.click()
    assert section.model.rows == ((short_date(FIRST_DAY), "50.5"),)


def test_undoing_a_replacement_puts_back_the_value_the_day_held(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    set_lean_mass(qtbot, window, dialog, "50.5")

    set_lean_mass(qtbot, window, dialog, "52.0")  # the same day: replaces the entry
    assert dialog.lean_mass_section.model.rows == ((short_date(FIRST_DAY), "52.0"),)

    with changed(qtbot, window):
        dialog.undo_button.click()

    assert dialog.lean_mass_section.model.rows == ((short_date(FIRST_DAY), "50.5"),)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("", "Enter the lean mass in kilograms."),
        ("abc", "Enter the lean mass in kilograms with at most one decimal, from 15 to 200."),
        ("50.55", "Enter the lean mass in kilograms with at most one decimal, from 15 to 200."),
        ("500", "Enter the lean mass in kilograms with at most one decimal, from 15 to 200."),
    ],
)
def test_a_value_that_cannot_be_read_is_told_on_the_field_and_nothing_is_saved(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, text: str, expected: str
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    section = dialog.lean_mass_section

    section.value_edit.setText(text)
    section.set_button.click()

    assert section.problem_text == expected
    assert dialog.undo_stack.count() == 0
    assert section.model.rowCount() == 0


def test_a_lean_mass_that_does_not_fit_the_weight_is_refused_on_the_field(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)  # weight 65.5 kg: 95% is 62.2 kg
    dialog = open_settings(qtbot, window)
    section = dialog.lean_mass_section
    section.value_edit.setText("64")
    section.from_edit.setDate(QDate(FIRST_DAY.year, FIRST_DAY.month, FIRST_DAY.day))

    with qtbot.waitSignal(window.nutrition.loader.lean_mass_refused, timeout=WAIT_MS):
        section.set_button.click()

    assert section.problem_text.startswith("The lean mass does not fit your weight. ")
    assert "between 40% and 95% of your weight" in section.problem_text
    assert dialog.undo_stack.count() == 0
    assert section.model.rowCount() == 0
    QTest.keyClicks(section.value_edit, "5")  # typing again clears the old message
    assert section.problem_text == ""


def test_removing_a_measurement_can_be_undone(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    dialog = open_settings(qtbot, window)
    set_lean_mass(qtbot, window, dialog, "50.5")
    section = dialog.lean_mass_section
    section.table.selectRow(0)
    assert section.remove_button.isEnabled()

    with changed(qtbot, window):
        section.remove_button.click()

    assert section.model.rowCount() == 0
    assert dialog.undo_stack.undoText() == "Remove lean mass"
    with changed(qtbot, window):
        dialog.undo_button.click()
    assert section.model.rows == ((short_date(FIRST_DAY), "50.5"),)


def test_the_dialog_opens_with_the_measurements_the_profile_already_holds(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    first = open_settings(qtbot, window)
    set_lean_mass(qtbot, window, first, "50.5")
    first.close()
    qtbot.waitUntil(lambda: window.nutrition.settings_dialog is None, timeout=WAIT_MS)

    reopened = open_settings(qtbot, window)

    assert reopened.lean_mass_section.model.rows == ((short_date(FIRST_DAY), "50.5"),)


def test_the_day_page_says_whether_the_lean_mass_is_estimated_or_measured(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)
    assert last_day_page(window).lean_mass_note_text == (
        "The limit on energy availability uses an estimate of your lean mass. You can enter a "
        "measured value in Nutrition settings."
    )

    dialog = open_settings(qtbot, window)
    set_lean_mass(qtbot, window, dialog, "50.5")
    dialog.close()
    qtbot.waitUntil(lambda: window.nutrition.settings_dialog is None, timeout=WAIT_MS)
    open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    qtbot.waitUntil(
        lambda: (
            last_day_page(window).lean_mass_note_text
            == "The limit on energy availability uses your measured lean mass."
        ),
        timeout=WAIT_MS,
    )
