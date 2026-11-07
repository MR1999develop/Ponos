# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The training settings dialog: time per weekday, the long day, the most a week, the session
types of each sport; suggestions where nothing is saved; what the plan cannot do is written in the
form."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QLocale
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionChoices, SessionType, WeeklyAvailability
from ponos_coach.ui.training_dialog import TrainingSettingsDialog
from ponos_coach.ui.training_draft import draft_of

SAVED = WeeklyAvailability((60, 0, 60, 0, 60, 90, 120), 6, 540)
CHOICES = SessionChoices.of({Sport.RUNNING: {SessionType.EASY, SessionType.LONG}})


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def saved_dialog(qtbot: QtBot) -> TrainingSettingsDialog:
    dialog = TrainingSettingsDialog(draft_of(SAVED, CHOICES), suggested=False)
    qtbot.addWidget(dialog)
    return dialog


def test_the_form_shows_what_is_saved(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    assert [spin.value() for spin in dialog.day_spins] == [60, 0, 60, 0, 60, 90, 120]
    assert dialog.long_day_combo.currentText() == "Sunday"
    assert dialog.week_hours_spin.value() == pytest.approx(9.0)
    ticked = {key for key, check in dialog.type_checks.items() if check.isChecked()}
    assert ticked == {(Sport.RUNNING, SessionType.EASY), (Sport.RUNNING, SessionType.LONG)}
    assert not dialog.note_is_shown
    assert dialog.draft() == draft_of(SAVED, CHOICES)


def test_each_weekday_has_its_name_and_its_unit(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    assert [spin.accessibleName() for spin in dialog.day_spins] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert all(spin.suffix() == " min" for spin in dialog.day_spins)
    assert dialog.week_hours_spin.suffix() == " h"


def test_nothing_changed_means_nothing_to_save_and_the_button_says_why(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    assert not dialog.save_button.isEnabled()
    assert dialog.save_button.toolTip() == "Nothing has changed."
    dialog.day_spins[1].setValue(45)
    assert dialog.save_button.isEnabled()
    dialog.day_spins[1].setValue(0)
    assert not dialog.save_button.isEnabled()  # back to what is saved


def test_changes_of_every_kind_reach_the_draft(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    dialog.day_spins[3].setValue(30)
    dialog.long_day_combo.setCurrentIndex(5)
    dialog.week_hours_spin.setValue(12.5)
    dialog.type_checks[(Sport.RUNNING, SessionType.THRESHOLD)].setChecked(True)
    dialog.type_checks[(Sport.CYCLING, SessionType.EASY)].setChecked(True)
    dialog.type_checks[(Sport.RUNNING, SessionType.LONG)].setChecked(False)

    draft = dialog.draft()
    assert draft.day_minutes[3] == 30
    assert draft.long_day == 5
    assert draft.max_week_minutes == 750
    assert draft.choices.for_sport(Sport.RUNNING) == {SessionType.EASY, SessionType.THRESHOLD}
    assert draft.choices.for_sport(Sport.CYCLING) == {SessionType.EASY}


def test_a_profile_with_nothing_saved_starts_from_suggestions_and_says_so(qtbot: QtBot) -> None:
    dialog = TrainingSettingsDialog(draft_of(None, SessionChoices.of({})), suggested=True)
    qtbot.addWidget(dialog)

    assert dialog.note_is_shown
    assert dialog.save_button.isEnabled()  # saving the suggestions is a choice too
    assert any(spin.value() > 0 for spin in dialog.day_spins)
    assert dialog.type_checks[(Sport.RUNNING, SessionType.EASY)].isChecked()


def test_no_time_at_all_is_written_in_the_form_and_does_not_stop_saving(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    dialog.week_hours_spin.setValue(0.0)

    assert dialog.hint_texts.startswith("No training time is set, so the plan cannot be made.")
    assert dialog.save_button.isEnabled()


def test_a_long_session_on_a_day_without_time_is_written_in_the_form(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    dialog.day_spins[6].setValue(0)

    assert dialog.hint_texts.startswith("The long day has no time")
    dialog.long_day_combo.setCurrentIndex(5)
    assert dialog.hint_texts == ""


def test_every_type_box_has_a_name_with_its_sport_and_its_type(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    names = {key: check.accessibleName() for key, check in dialog.type_checks.items()}

    assert names[(Sport.RUNNING, SessionType.THRESHOLD)] == "Running: Threshold"
    assert names[(Sport.TRAIL, SessionType.VO2MAX)] == "Trail running: VO2max"
    assert len(set(names.values())) == len(names) == 5 * 8


def test_cancel_changes_nothing(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)
    accepted: list[bool] = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog.day_spins[0].setValue(10)
    dialog.reject()

    assert accepted == []
