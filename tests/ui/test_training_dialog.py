# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The training settings dialog: the days available, the sessions per week, the long day, the most
a week, the session types of each sport; suggestions where nothing is saved; what the plan cannot
do is written in the form."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QLocale
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import SessionChoices, SessionType, WeeklyAvailability
from ponos_coach.ui.training_dialog import TrainingSettingsDialog
from ponos_coach.ui.training_draft import draft_of

SAVED = WeeklyAvailability(frozenset({0, 2, 4, 5, 6}), 3, 6, 540)
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

    assert [check.isChecked() for check in dialog.day_checks] == [
        True,
        False,
        True,
        False,
        True,
        True,
        True,
    ]
    assert dialog.sessions_spin.value() == 3
    assert dialog.long_day_combo.currentText() == "Sunday"
    assert dialog.week_hours_spin.value() == pytest.approx(9.0)
    ticked = {key for key, check in dialog.type_checks.items() if check.isChecked()}
    assert ticked == {(Sport.RUNNING, SessionType.EASY), (Sport.RUNNING, SessionType.LONG)}
    assert not dialog.note_is_shown
    assert dialog.draft() == draft_of(SAVED, CHOICES)


def test_each_weekday_has_its_name_and_the_week_its_unit(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    assert [check.accessibleName() for check in dialog.day_checks] == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    assert [check.text() for check in dialog.day_checks] == [
        check.accessibleName() for check in dialog.day_checks
    ]
    assert dialog.week_hours_spin.suffix() == " h"


def test_the_sessions_cannot_pass_the_ticked_days_and_the_long_day_is_one_of_them(
    qtbot: QtBot,
) -> None:
    dialog = saved_dialog(qtbot)

    assert dialog.sessions_spin.maximum() == 5
    assert [dialog.long_day_combo.itemText(i) for i in range(dialog.long_day_combo.count())] == [
        "Monday",
        "Wednesday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    dialog.day_checks[6].setChecked(False)  # the long day goes
    assert dialog.sessions_spin.maximum() == 4
    assert dialog.long_day_combo.currentText() == "Saturday"
    assert dialog.draft().long_day == 5
    dialog.day_checks[1].setChecked(True)
    assert dialog.sessions_spin.maximum() == 5


def test_with_no_day_ticked_the_fields_that_depend_on_days_are_off_and_the_form_says_so(
    qtbot: QtBot,
) -> None:
    dialog = saved_dialog(qtbot)

    for check in dialog.day_checks:
        check.setChecked(False)

    assert not dialog.sessions_spin.isEnabled()
    assert not dialog.long_day_combo.isEnabled()
    assert dialog.hint_texts.startswith("No training time is set, so the plan cannot be made.")
    assert dialog.can_save()
    dialog.day_checks[2].setChecked(True)
    assert dialog.sessions_spin.isEnabled()
    assert dialog.long_day_combo.currentText() == "Wednesday"


def test_nothing_changed_means_nothing_to_save_and_the_button_says_why(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    assert not dialog.can_save()
    assert dialog.save_blocked_reason() == "Nothing has changed."
    dialog.day_checks[1].setChecked(True)
    assert dialog.can_save()
    dialog.day_checks[1].setChecked(False)
    assert not dialog.can_save()  # back to what is saved


def test_changes_of_every_kind_reach_the_draft(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    dialog.day_checks[3].setChecked(True)
    dialog.sessions_spin.setValue(4)
    dialog.long_day_combo.setCurrentIndex(dialog.long_day_combo.findData(5))
    dialog.week_hours_spin.setValue(12.5)
    dialog.type_checks[(Sport.RUNNING, SessionType.THRESHOLD)].setChecked(True)
    dialog.type_checks[(Sport.CYCLING, SessionType.EASY)].setChecked(True)
    dialog.type_checks[(Sport.RUNNING, SessionType.LONG)].setChecked(False)

    draft = dialog.draft()
    assert draft.days == {0, 2, 3, 4, 5, 6}
    assert draft.sessions == 4
    assert draft.long_day == 5
    assert draft.max_week_minutes == 750
    assert draft.choices.for_sport(Sport.RUNNING) == {SessionType.EASY, SessionType.THRESHOLD}
    assert draft.choices.for_sport(Sport.CYCLING) == {SessionType.EASY}


def test_a_profile_with_nothing_saved_starts_from_suggestions_and_says_so(qtbot: QtBot) -> None:
    dialog = TrainingSettingsDialog(draft_of(None, SessionChoices.of({})), suggested=True)
    qtbot.addWidget(dialog)

    assert dialog.note_is_shown
    assert dialog.can_save()  # saving the suggestions is a choice too
    assert any(check.isChecked() for check in dialog.day_checks)
    assert dialog.type_checks[(Sport.RUNNING, SessionType.EASY)].isChecked()


def test_no_time_at_all_is_written_in_the_form_and_does_not_stop_saving(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    dialog.week_hours_spin.setValue(0.0)

    assert dialog.hint_texts.startswith("No training time is set, so the plan cannot be made.")
    assert dialog.can_save()


def test_every_type_box_has_a_name_with_its_sport_and_its_type(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)

    names = {key: check.accessibleName() for key, check in dialog.type_checks.items()}

    assert names[(Sport.RUNNING, SessionType.THRESHOLD)] == "Running: Threshold"
    assert names[(Sport.TRAIL, SessionType.VO2MAX)] == "Trail running: VO2max"
    assert len(set(names.values())) == len(names) == 5 * 8


def test_nothing_is_saved_until_save_is_clicked_and_the_form_knows_what_is_unsaved(
    qtbot: QtBot,
) -> None:
    dialog = saved_dialog(qtbot)
    requested: list[bool] = []
    dialog.save_requested.connect(lambda: requested.append(True))
    assert not dialog.has_unsaved_changes()

    dialog.day_checks[0].setChecked(False)

    assert dialog.has_unsaved_changes()
    assert requested == []
    dialog.request_save()
    assert requested == [True]


def test_showing_what_is_saved_leaves_nothing_to_save(qtbot: QtBot) -> None:
    dialog = saved_dialog(qtbot)
    dialog.day_checks[0].setChecked(False)
    edited = dialog.draft()

    dialog.show_saved(edited, suggested=False)

    assert not dialog.has_unsaved_changes()
    assert dialog.draft() == edited
    assert not dialog.can_save()
