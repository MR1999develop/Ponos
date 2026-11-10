# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""New profile: cycling and other-activities thresholds and the threshold pace, from the dialog
through the creation to "Edit profile"."""

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


def test_the_dialog_has_the_three_new_fields_empty_with_the_defaults_as_placeholders(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    assert dialog.bike_edit.text() == ""
    assert dialog.other_edit.text() == ""
    assert dialog.pace_edit.text() == ""
    assert dialog.bike_edit.placeholderText() == "default: running - 4 (167 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running (171 bpm)"


def test_the_placeholders_follow_the_heart_rates_entered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)

    dialog.threshold_spin.setValue(160)

    assert dialog.bike_edit.placeholderText() == "default: running - 4 (156 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running (160 bpm)"


def test_tab_goes_through_the_new_fields_in_visual_order(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, dialog = open_dialog(qtbot, tmp_path, settings)
    dialog.show()
    dialog.threshold_spin.setFocus()

    for expected in (dialog.bike_edit, dialog.other_edit, dialog.pace_edit):
        QTest.keyClick(dialog.focusWidget(), Qt.Key.Key_Tab)
        assert dialog.focusWidget() is expected


def test_the_values_typed_are_kept_by_creation_and_shown_by_edit_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, dialog = open_dialog(qtbot, tmp_path, settings)
    QTest.keyClicks(dialog.bike_edit, "160")
    QTest.keyClicks(dialog.other_edit, "168")
    QTest.keyClicks(dialog.pace_edit, "5.30")

    submit(dialog)
    edit = wait_created(qtbot, window)

    assert edit.bike_edit.text() == "160"
    assert edit.other_edit.text() == "168"
    assert edit.pace_edit.text() == "5:30"


def test_empty_fields_create_a_profile_with_the_defaults_and_no_pace(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, dialog = open_dialog(qtbot, tmp_path, settings)

    submit(dialog)
    edit = wait_created(qtbot, window)

    assert edit.bike_edit.text() == ""
    assert edit.other_edit.text() == ""
    assert edit.pace_edit.text() == ""


@pytest.mark.parametrize(
    ("field", "text"),
    [
        (Field.BIKE_THRESHOLD, "300"),
        (Field.BIKE_THRESHOLD, "30"),
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
        Field.BIKE_THRESHOLD: dialog.bike_edit,
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
