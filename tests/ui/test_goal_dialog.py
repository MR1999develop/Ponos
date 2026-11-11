# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The goal dialog: problems written under their field as soon as they appear, the save button off
until they are solved, the same form for a race and for a form goal, Cancel changes nothing."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from PySide6.QtCore import QLocale
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal
from ponos_coach.ui.birth_date_edit import to_qdate
from ponos_coach.ui.goal_dialog import GoalDialog
from ponos_coach.ui.goal_draft import GoalDraft, GoalField, GoalFormKind, default_draft

TODAY = date(2026, 6, 30)
RACE_DAY = date(2026, 9, 20)
OTHER_DAY = date(2026, 10, 4)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def saved_race(goal_id: str = "first", day: date = RACE_DAY) -> RaceGoal:
    return RaceGoal(GoalId(goal_id), day, Priority.A, Sport.RUNNING, 42_195, 12_600)


def new_race_dialog(qtbot: QtBot, goals: list[RaceGoal] | None = None) -> GoalDialog:
    dialog = GoalDialog(default_draft(GoalFormKind.RACE, RACE_DAY), goals or [], TODAY)
    qtbot.addWidget(dialog)
    return dialog


def test_a_new_race_starts_valid_with_its_verb_and_title(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    assert dialog.windowTitle() == "Add race"
    assert dialog.save_button.text() == "Add goal"
    assert dialog.save_button.isEnabled()
    assert dialog.issues() == ()
    assert dialog.draft() == default_draft(GoalFormKind.RACE, RACE_DAY)


def test_the_common_distances_are_offered_and_any_other_is_typed_in_kilometres(
    qtbot: QtBot,
) -> None:
    dialog = new_race_dialog(qtbot)

    assert [dialog.distance_combo.itemText(i) for i in range(dialog.distance_combo.count())] == [
        "5 km",
        "10 km",
        "Half marathon",
        "Marathon",
        "Other distance",
    ]
    assert not dialog.custom_distance_spin.isEnabled()
    dialog.distance_combo.setCurrentIndex(dialog.distance_combo.count() - 1)
    dialog.custom_distance_spin.setValue(12.5)

    assert dialog.custom_distance_spin.isEnabled()
    assert dialog.draft().distance_m == 12_500
    dialog.distance_combo.setCurrentIndex(0)
    assert dialog.draft().distance_m == 5_000
    assert not dialog.custom_distance_spin.isEnabled()


def test_a_saved_race_with_another_distance_opens_on_other_distance(qtbot: QtBot) -> None:
    race = RaceGoal(GoalId("x"), RACE_DAY, Priority.C, Sport.TRAIL, 32_500, None)
    dialog = GoalDialog.for_goal(race, [race], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Change race"
    assert dialog.save_button.text() == "Save goal"
    assert dialog.distance_combo.currentText() == "Other distance"
    assert dialog.custom_distance_spin.value() == pytest.approx(32.5)
    assert dialog.draft() == GoalDraft(
        GoalFormKind.RACE, RACE_DAY, Priority.C, Sport.TRAIL, 32_500, ""
    )


def test_a_day_that_is_not_after_today_is_written_under_the_date_and_blocks_saving(
    qtbot: QtBot,
) -> None:
    dialog = new_race_dialog(qtbot)

    dialog.date_edit.setDate(to_qdate(TODAY))

    assert (
        dialog.error_text(GoalField.DATE) == "Choose a day after today: the plan starts tomorrow."
    )
    assert not dialog.save_button.isEnabled()
    assert dialog.save_button.toolTip() == "Solve the problem written under the field first."
    dialog.date_edit.setDate(to_qdate(date(2026, 7, 1)))
    assert dialog.error_text(GoalField.DATE) == ""
    assert dialog.save_button.isEnabled()


def test_a_second_race_on_a_day_with_a_race_is_a_problem_of_the_date(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot, [saved_race()])

    assert dialog.error_text(GoalField.DATE) == (
        "Another race is already on this day. Choose another day."
    )
    assert not dialog.save_button.isEnabled()
    dialog.date_edit.setDate(to_qdate(OTHER_DAY))
    assert dialog.error_text(GoalField.DATE) == ""
    assert dialog.save_button.isEnabled()


def test_changing_a_race_does_not_collide_with_itself(qtbot: QtBot) -> None:
    race = saved_race()
    dialog = GoalDialog.for_goal(race, [race], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.error_text(GoalField.DATE) == ""
    assert dialog.save_button.isEnabled()


def test_a_form_goal_may_be_on_the_day_of_a_race(qtbot: QtBot) -> None:
    dialog = GoalDialog(default_draft(GoalFormKind.FORM, RACE_DAY), [saved_race()], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.save_button.isEnabled()
    assert dialog.windowTitle() == "Add form goal"


def test_a_target_time_that_is_not_a_time_is_a_problem_of_that_field(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    QTest.keyClicks(dialog.target_time_edit, "fast")

    assert dialog.error_text(GoalField.TARGET_TIME) == (
        "Write the time as h:mm:ss, for example 1:45:00."
    )
    assert not dialog.save_button.isEnabled()
    dialog.target_time_edit.setText("3:30:00")
    assert dialog.error_text(GoalField.TARGET_TIME) == ""
    assert dialog.draft().target_time_text == "3:30:00"
    assert dialog.save_button.isEnabled()


def test_a_target_time_below_a_minute_is_out_of_range(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    dialog.target_time_edit.setText("0:00:30")

    assert dialog.error_text(GoalField.TARGET_TIME) == (
        "The target time must be between 1 minute and 7 days."
    )


def test_the_form_goal_asks_only_for_the_day_and_the_ctl(qtbot: QtBot) -> None:
    dialog = GoalDialog(default_draft(GoalFormKind.FORM, RACE_DAY), [], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.ctl_spin.minimum() == 0.0
    assert dialog.ctl_spin.maximum() == 200.0
    dialog.ctl_spin.setValue(72.0)
    assert dialog.draft().ctl == 72.0
    assert dialog.draft().kind is GoalFormKind.FORM
    assert dialog.error_text(GoalField.TARGET_TIME) == ""


def test_the_priority_choices_say_what_each_means(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    texts = [dialog.priority_combo.itemText(i) for i in range(3)]

    assert texts == ["A: the goal of the season", "B: an important race", "C: a race for training"]
    dialog.priority_combo.setCurrentIndex(2)
    assert dialog.draft().priority is Priority.C


def test_the_sports_are_the_ones_a_plan_can_be_made_for(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    names = [dialog.sport_combo.itemText(i) for i in range(dialog.sport_combo.count())]

    assert names == ["Running", "Trail running", "Cycling", "Hiking", "Walking"]


def test_cancel_accepts_nothing(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)
    accepted: list[bool] = []
    dialog.accepted.connect(lambda: accepted.append(True))
    box = dialog.findChild(QDialogButtonBox)
    assert box is not None

    box.button(QDialogButtonBox.StandardButton.Cancel).click()

    assert accepted == []
    assert dialog.result() == 0


def test_every_field_has_a_name_for_the_screen_reader_and_the_errors_too(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    for widget in (
        dialog.date_edit,
        dialog.priority_combo,
        dialog.sport_combo,
        dialog.distance_combo,
        dialog.custom_distance_spin,
        dialog.target_time_edit,
    ):
        assert widget.accessibleName()
    errors = [w for w in dialog.findChildren(QLabel) if w.objectName().endswith("Error")]
    assert errors
    assert all(label.accessibleName() for label in errors)
    assert dialog.target_time_edit.accessibleDescription() == "Optional. For example 1:45:00."


def test_the_form_can_be_filled_with_the_keyboard_alone_in_reading_order(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)
    wanted = [
        "goalDate",
        "goalPriority",
        "goalSport",
        "goalDistance",
        "goalDistanceKm",
        "goalTargetTime",
    ]
    chain: list[str] = []
    widget: QWidget | None = dialog.date_edit
    for _ in range(40):
        if widget is None:
            break
        if widget.objectName() in wanted and widget.objectName() not in chain:
            chain.append(widget.objectName())
        widget = widget.nextInFocusChain()

    assert chain == wanted


# --- The elevation gain ------------------------------------------------------------------------
def test_the_elevation_gain_is_a_field_of_the_race_under_the_distance(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "Elevation gain (m)" in labels
    assert dialog.ascent_edit.text() == ""
    assert dialog.draft().ascent_text == ""
    assert dialog.error_text(GoalField.ASCENT) == ""
    assert dialog.save_button.isEnabled()  # empty means a flat race


def test_a_typed_elevation_gain_is_read_tolerantly_and_goes_into_the_draft(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)

    QTest.keyClicks(dialog.ascent_edit, "1 200 m")

    assert dialog.error_text(GoalField.ASCENT) == ""
    assert dialog.draft().ascent_text == "1 200 m"
    assert dialog.save_button.isEnabled()


def test_an_elevation_gain_that_is_not_whole_metres_is_written_under_its_field(
    qtbot: QtBot,
) -> None:
    dialog = new_race_dialog(qtbot)

    dialog.ascent_edit.setText("a lot")

    assert dialog.error_text(GoalField.ASCENT) == (
        "Write the elevation gain in whole metres, for example 1200."
    )
    assert not dialog.save_button.isEnabled()
    dialog.ascent_edit.setText("")
    assert dialog.error_text(GoalField.ASCENT) == ""
    assert dialog.save_button.isEnabled()


def test_an_elevation_gain_too_large_for_the_distance_is_a_problem_of_its_field(
    qtbot: QtBot,
) -> None:
    dialog = new_race_dialog(qtbot)

    dialog.ascent_edit.setText("30000")  # above half of a marathon

    assert dialog.error_text(GoalField.ASCENT).startswith("The elevation gain is too large")
    dialog.ascent_edit.setText("30001")
    assert (
        dialog.error_text(GoalField.ASCENT) == "The elevation gain must be between 0 and 30,000 m."
    )
    assert not dialog.save_button.isEnabled()


def test_a_saved_race_opens_with_its_elevation_gain(qtbot: QtBot) -> None:
    race = RaceGoal(GoalId("x"), RACE_DAY, Priority.A, Sport.TRAIL, 32_500, None, 1_800)
    dialog = GoalDialog.for_goal(race, [race], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.ascent_edit.text() == "1800"
    assert dialog.draft().ascent_text == "1800"


# --- A cycling race ----------------------------------------------------------------------------
def choose_sport(dialog: GoalDialog, sport: Sport) -> None:
    dialog.sport_combo.setCurrentIndex(dialog.sport_combo.findData(sport))


def test_a_cycling_race_turns_off_the_target_time_and_says_why(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)
    dialog.target_time_edit.setText("3:30:00")

    choose_sport(dialog, Sport.CYCLING)

    assert not dialog.target_time_edit.isEnabled()
    assert dialog.target_time_edit.placeholderText() == "Not used for cycling races"
    assert "distance and elevation gain" in dialog.target_time_edit.accessibleDescription()
    assert dialog.save_button.isEnabled()


def test_a_cycling_race_takes_its_distance_in_kilometres_from_the_chosen_one(
    qtbot: QtBot,
) -> None:
    dialog = new_race_dialog(qtbot)  # a marathon by default

    choose_sport(dialog, Sport.CYCLING)

    assert dialog.distance_combo.currentText() == "Other distance"
    assert not dialog.distance_combo.isEnabled()
    assert dialog.custom_distance_spin.isEnabled()
    assert dialog.custom_distance_spin.value() == pytest.approx(42.195)
    dialog.custom_distance_spin.setValue(110.0)
    dialog.ascent_edit.setText("1500")
    draft = dialog.draft()
    assert (draft.sport, draft.distance_m, draft.ascent_text) == (Sport.CYCLING, 110_000, "1500")


def test_back_to_running_the_target_time_and_the_distances_return(qtbot: QtBot) -> None:
    dialog = new_race_dialog(qtbot)
    choose_sport(dialog, Sport.CYCLING)

    choose_sport(dialog, Sport.RUNNING)

    assert dialog.target_time_edit.isEnabled()
    assert dialog.target_time_edit.placeholderText() == "h:mm:ss"
    assert dialog.distance_combo.isEnabled()


def test_a_saved_cycling_race_opens_with_distance_and_climb_and_no_time(qtbot: QtBot) -> None:
    race = RaceGoal(GoalId("x"), RACE_DAY, Priority.A, Sport.CYCLING, 130_000, 18_000, 2_100)
    dialog = GoalDialog.for_goal(race, [race], TODAY)
    qtbot.addWidget(dialog)

    assert dialog.sport_combo.currentData() is Sport.CYCLING
    assert dialog.custom_distance_spin.value() == pytest.approx(130.0)
    assert dialog.ascent_edit.text() == "2100"
    assert dialog.target_time_edit.text() == ""
    assert not dialog.target_time_edit.isEnabled()
