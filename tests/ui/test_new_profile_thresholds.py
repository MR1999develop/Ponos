# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""New profile: LT1 and LT2 of the two groups and the threshold pace, from the dialog through
the creation to "Edit profile"."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import Field
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from tests.ui.workspace_support import (
    WAIT_MS,
    compose,
    fill_new_profile,
    make_settings,
)

PROFILE = "Atleta di prova"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def open_dialog(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> tuple[MainWindow, NewProfileDialog]:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    fill_new_profile(window, dialog, PROFILE)
    return window, dialog


def submit(dialog: NewProfileDialog) -> None:
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)


def wait_created(qtbot: QtBot, window: MainWindow) -> ProfileEditDialog:
    qtbot.waitUntil(
        lambda: (
            window.controller.profile is not None
            and isinstance(window.controller.overview_state, Ready)
        ),
        timeout=WAIT_MS,
    )
    qtbot.waitUntil(window.edit_profile_action.isEnabled, timeout=WAIT_MS)
    window.edit_profile_action.trigger()
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is not None, timeout=WAIT_MS)
    edit = window.profile_dialogs.edit_dialog
    assert edit is not None
    return edit


def test_the_dialog_has_the_optional_fields_empty_with_the_defaults_as_placeholders(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    assert dialog.run_lt1_edit.text() == ""
    assert dialog.other_lt1_edit.text() == ""
    assert dialog.other_edit.text() == ""
    assert dialog.pace_edit.text() == ""
    assert dialog.run_lt1_edit.placeholderText() == "default: 83% of LT2 (142 bpm)"
    assert dialog.other_lt1_edit.placeholderText() == "default: 83% of LT2 (142 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running LT2 (171 bpm)"


def test_the_placeholders_follow_the_heart_rates_entered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    dialog.threshold_spin.setValue(160)

    assert dialog.run_lt1_edit.placeholderText() == "default: 83% of LT2 (133 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running LT2 (160 bpm)"


def test_the_default_lt1_of_the_other_sports_follows_their_lt2_as_typed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    QTest.keyClicks(dialog.other_edit, "150")

    assert dialog.other_lt1_edit.placeholderText() == "default: 83% of LT2 (125 bpm)"
    assert dialog.run_lt1_edit.placeholderText() == "default: 83% of LT2 (142 bpm)"


def test_each_lt1_and_lt2_explains_itself_in_a_tooltip(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    for lt1 in (dialog.run_lt1_edit, dialog.other_lt1_edit):
        assert "aerobic threshold" in lt1.toolTip()
        assert "talk" in lt1.toolTip()  # how to estimate it
        assert "83%" in lt1.toolTip()  # what an empty field means
    for lt2 in (dialog.threshold_spin, dialog.other_edit):
        assert "lactate threshold" in lt2.toolTip()


def test_the_fields_of_the_two_groups_are_told_apart_for_screen_readers(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    names = [
        w.accessibleName()
        for w in (
            dialog.run_lt1_edit,
            dialog.threshold_spin,
            dialog.other_lt1_edit,
            dialog.other_edit,
        )
    ]

    assert len(set(names)) == 4
    assert names[0].startswith("Running and trail") and names[2].startswith("Other sports")


def test_tab_goes_through_the_fields_in_visual_order(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)
    dialog.show()
    dialog.run_lt1_edit.setFocus()

    for expected in (
        dialog.threshold_spin,
        dialog.other_lt1_edit,
        dialog.other_edit,
        dialog.pace_edit,
    ):
        QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
        assert dialog.focusWidget() is expected


def test_the_values_typed_are_kept_by_creation_and_shown_by_edit_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, dialog = open_dialog(qtbot, tmp_path, settings)
    QTest.keyClicks(dialog.run_lt1_edit, "140")
    QTest.keyClicks(dialog.other_edit, "168")
    QTest.keyClicks(dialog.other_lt1_edit, "130")
    QTest.keyClicks(dialog.pace_edit, "5.30")

    submit(dialog)
    edit = wait_created(qtbot, window)

    assert edit.run_lt1_edit.text() == "140"
    assert edit.other_edit.text() == "168"
    assert edit.other_lt1_edit.text() == "130"
    assert edit.pace_edit.text() == "5:30"


def test_empty_fields_create_a_profile_with_the_defaults_and_no_pace(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, dialog = open_dialog(qtbot, tmp_path, settings)

    submit(dialog)
    edit = wait_created(qtbot, window)

    assert edit.run_lt1_edit.text() == ""
    assert edit.other_lt1_edit.text() == ""
    assert edit.other_edit.text() == ""
    assert edit.pace_edit.text() == ""


@pytest.mark.parametrize(
    ("field", "text"),
    [
        (Field.RUN_LT1, "300"),
        (Field.RUN_LT1, "30"),
        (Field.RUN_LT1, "171"),
        (Field.OTHER_LT1, "171"),
        (Field.OTHER_THRESHOLD, "195"),
        (Field.THRESHOLD_PACE, "fast"),
        (Field.THRESHOLD_PACE, "5:75"),
    ],
)
def test_an_invalid_value_is_reported_on_its_field_and_nothing_is_created(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, field: Field, text: str
) -> None:
    window, dialog = open_dialog(qtbot, tmp_path, settings)
    edit = {
        Field.RUN_LT1: dialog.run_lt1_edit,
        Field.OTHER_LT1: dialog.other_lt1_edit,
        Field.OTHER_THRESHOLD: dialog.other_edit,
        Field.THRESHOLD_PACE: dialog.pace_edit,
    }[field]
    edit.setText(text)
    edit.textEdited.emit(text)
    assert dialog.error_text(field) == ""  # the first error appears on submit

    submit(dialog)

    assert dialog.error_text(field) != ""
    assert text not in dialog.error_text(field)  # never the typed value
    assert window.controller.profile is None
