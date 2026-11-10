# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Goals, training settings and the plan in the window (T-UI-01 goal -> plan -> calendar, T-UI-03).

From the Composition Root, with a made-up profile and activities in June 2026 (today is 30 June):
the goals go through the dialogs and the undo stack, the plan is rebuilt by the real engine on the
data thread, and its state shows in the Goals section, the calendar, the PMC and the status bar.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtWidgets import QLabel, QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal, SessionType, WeeklyAvailability
from ponos_coach.planning.domain import Block, BlockRole, Phase, Plan, PlanDay, PlannedSession
from ponos_coach.planning.ports import FrozenDay
from ponos_coach.ui.birth_date_edit import to_qdate
from ponos_coach.ui.calendar_model import first_of_month
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.goal_draft import GoalField
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.plan_presentation import NoPlanYet, PlanAvailable, PlanRefusedNow
from ponos_coach.ui.plan_undo import OutcomeCell
from ponos_coach.ui.settings_page import SettingsCategory
from tests.ui.goals_support import (
    EVERY_DAY,
    RACE_DAY,
    add_race,
    choices_of,
    goals_of,
    race_ids,
    races_of,
    ready_window,
    select_goal,
    set_training,
    wait_for,
    wait_idle,
    wait_plan,
    wait_refusal,
)
from tests.ui.nutrition_support import DAWN_AND_LUNCH_DAY, cell_of
from tests.ui.workspace_support import TODAY, answer_unsaved, compose, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def plan_window(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    """A profile with time to train and every session type, and no goal yet."""
    window = ready_window(qtbot, tmp_path, settings)
    set_training(qtbot, window)
    return window


def undo(window: MainWindow) -> None:
    action = window.plan.actions["undo"]
    assert action.isEnabled()
    action.trigger()


def redo(window: MainWindow) -> None:
    action = window.plan.actions["redo"]
    assert action.isEnabled()
    action.trigger()


# --- The section and the menu -----------------------------------------------------------------


def test_the_goals_window_opens_from_the_menu_and_the_commands_have_their_reasons(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)

    window.goals_window_action.trigger()

    assert window.goals_window.isVisible()
    assert window.goals_window.windowTitle() == "Goals and plan status"
    assert window.goals_page.empty_text.startswith("No goals yet.")
    actions = window.plan.actions
    assert actions["add_race"].isEnabled()
    assert actions["training"].isEnabled()
    for name in ("change_goal", "remove_goal"):
        assert not actions[name].isEnabled()
        assert actions[name].toolTip() == "Select a day with a goal in the Calendar first."
    assert not actions["undo"].isEnabled()
    assert actions["undo"].toolTip() == "There is no change to the goals to take back."


def test_there_is_no_plan_nor_calendar_menu_and_the_plan_shortcuts_stay(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    # The commands live in the calendar's day menu (right click); their shortcuts still work.
    window = ready_window(qtbot, tmp_path, settings)

    titles = [a.text() for a in window.menuBar().actions()]
    assert "Plan" not in titles
    assert "Calendar" not in titles
    actions = window.plan.actions
    assert [
        (actions[n].text(), actions[n].shortcut().toString())
        for n in ("add_race", "add_form_goal", "change_goal", "remove_goal", "training")
    ] == [
        ("Add race...", "Ctrl+R"),
        ("Add form goal...", "Ctrl+Shift+F"),
        ("Change goal...", "Ctrl+Shift+E"),
        ("Remove goal", "Ctrl+Del"),
        ("Training settings...", "Ctrl+Shift+T"),
    ]


def test_without_a_profile_the_plan_commands_are_off_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    for name in ("add_race", "add_form_goal", "training"):
        action = window.plan.actions[name]
        assert not action.isEnabled()
        assert action.toolTip() == "Create or open a profile first."
    assert isinstance(window.plan.state, NoPlanYet)


# --- A goal, its plan and the undo ------------------------------------------------------------


def test_a_race_added_in_the_dialog_is_in_the_table_and_the_plan_is_built_for_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)

    add_race(qtbot, window, RACE_DAY, target="3:20:00")
    plan = wait_plan(qtbot, window, race_ids(window))

    (race,) = races_of(window)
    assert (race.day, race.priority, race.sport, race.distance_m) == (
        RACE_DAY,
        Priority.A,
        Sport.RUNNING,
        42_195,
    )
    assert race.target_time_s == 12_000
    model = window.goals_page.proxy
    assert model.rowCount() == 1
    assert [str(model.index(0, c).data()) for c in range(5)] == [
        "9/22/26",
        "Race A",
        "Running",
        "42.195",
        "3:20:00",
    ]
    assert model.index(0, 5).data() in {
        "Realistic",
        "Ambitious",
        "Not reachable",
        "Cannot be estimated",
    }
    assert plan.generated_on == TODAY
    assert window.goals_page.notice_texts == () or all(
        text.startswith("Warning") for text in window.goals_page.notice_texts
    )


def test_a_second_goal_defaults_to_priority_b_when_there_is_already_an_a_race(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)

    window.plan.actions["add_race"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)

    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.draft().priority is Priority.B
    dialog.reject()


def test_taking_back_an_added_race_removes_it_and_redo_adds_the_same_goal_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY, target="3:20:00")
    (added,) = goals_of(window)
    wait_plan(qtbot, window, race_ids(window))

    assert window.plan.actions["undo"].text() == "Undo Add goal"
    undo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == ())
    wait_refusal(qtbot, window, "You have no goal yet.")
    redo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and len(goals_of(window)) == 1)

    assert goals_of(window) == (added,)  # the same id, the same values
    wait_plan(qtbot, window, race_ids(window))


def test_changing_a_race_and_taking_it_back_restores_the_old_version_exactly(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    (original,) = races_of(window)
    select_goal(qtbot, window, original.goal_id.value)
    assert window.plan.actions["change_goal"].isEnabled()

    window.plan.actions["change_goal"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.windowTitle() == "Change race"
    dialog.target_time_edit.setText("3:15:00")
    dialog.priority_combo.setCurrentIndex(1)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0] != original)

    (changed,) = races_of(window)
    assert changed.goal_id == original.goal_id
    assert (changed.target_time_s, changed.priority) == (11_700, Priority.B)
    undo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0] == original)
    redo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0] == changed)


def test_taking_a_change_back_restores_the_real_old_version_not_the_one_the_dialog_saw(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    (original,) = races_of(window)
    window.plan.change_goal(original.goal_id.value)
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.target_time_edit.setText("3:15:00")
    # The goal is changed meanwhile, not through the dialog.
    outside = replace(original, priority=Priority.C)
    assert window.plan._jobs.change_goal(outside, OutcomeCell())
    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0] == outside)

    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0].target_time_s == 11_700)
    undo(window)

    wait_for(qtbot, lambda: window.plan.is_idle and races_of(window)[0] == outside)


def test_taking_back_the_training_time_restores_the_real_previous_one(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    window.plan.actions["training"].trigger()
    wait_for(qtbot, lambda: window.plan.training_dialog is not None)
    dialog = window.plan.training_dialog
    assert dialog is not None
    outside = WeeklyAvailability(EVERY_DAY, 7, 6, 300)
    assert window.plan._jobs.set_availability(outside, OutcomeCell())
    wait_for(qtbot, lambda: window.plan.is_idle and _availability_of(window) == outside)

    dialog.week_hours_spin.setValue(9.0)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and _availability_of(window) != outside)
    undo(window)

    wait_for(qtbot, lambda: window.plan.is_idle and _availability_of(window) == outside)


def _availability_of(window: MainWindow) -> WeeklyAvailability | None:
    snapshot = window.plan.snapshot
    return None if snapshot is None else snapshot.availability


def test_a_race_that_loses_its_day_meanwhile_keeps_the_dialog_open_with_the_problem_written(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    window.plan.actions["add_race"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.date_edit.setDate(to_qdate(RACE_DAY))
    other = RaceGoal(GoalId("outside"), RACE_DAY, Priority.B, Sport.RUNNING, 10_000, None)
    assert window.plan._jobs.add_goal(other)
    wait_for(qtbot, lambda: window.plan.is_idle and len(goals_of(window)) == 1)
    pushed = window.plan.undo_stack.count()

    dialog.save_button.click()

    def reopened() -> bool:
        current = window.plan.goal_dialog
        return current is not None and current is not dialog

    wait_for(qtbot, reopened)
    again = window.plan.goal_dialog
    assert again is not None
    assert again.error_text(GoalField.DATE) == (
        "Another race is already on this day. Choose another day."
    )
    assert window.plan.undo_stack.count() == pushed
    again.reject()


def test_saving_a_race_without_changing_anything_adds_nothing_to_the_stack(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    (race,) = races_of(window)
    pushed = window.plan.undo_stack.count()

    window.plan.change_goal(race.goal_id.value)
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.save_button.click()

    assert window.plan.undo_stack.count() == pushed


def test_removing_a_goal_asks_nothing_and_taking_it_back_puts_the_same_goal_in_its_place(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY, target="3:20:00")
    (race,) = races_of(window)
    select_goal(qtbot, window, race.goal_id.value)

    window.plan.actions["remove_goal"].trigger()
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == ())

    assert not [b for b in window.findChildren(QMessageBox) if b.isVisible()]
    assert window.plan.actions["undo"].text() == "Undo Remove goal"
    undo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == (race,))


def test_a_form_goal_is_added_with_its_ctl_and_listed_without_a_race_distance(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)

    window.plan.actions["add_form_goal"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.ctl_spin.setValue(60.0)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and len(goals_of(window)) == 1)

    row = [str(window.goals_page.proxy.index(0, c).data()) for c in range(6)]
    assert row[1] == "Form goal"
    assert row[2:] == ["", "", "", ""]


# --- Domain errors on the field ---------------------------------------------------------------


def test_a_second_race_on_the_same_day_is_refused_on_the_date_field(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)

    window.plan.actions["add_race"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.priority_combo.setCurrentIndex(0)
    dialog.date_edit.setDate(to_qdate(RACE_DAY))

    assert dialog.error_text(GoalField.DATE) == (
        "Another race is already on this day. Choose another day."
    )
    assert not dialog.save_button.isEnabled()
    dialog.reject()
    assert len(goals_of(window)) == 1


def test_a_day_that_is_not_in_the_future_is_refused_on_the_date_field(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    pushed = window.plan.undo_stack.count()
    window.plan.actions["add_race"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.date_edit.setDate(to_qdate(TODAY))

    assert (
        dialog.error_text(GoalField.DATE) == "Choose a day after today: the plan starts tomorrow."
    )
    assert not dialog.save_button.isEnabled()
    dialog.reject()
    assert goals_of(window) == ()
    assert window.plan.undo_stack.count() == pushed


def test_a_refusal_of_the_store_is_a_message_with_a_remedy_and_the_screen_shows_the_store(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    (first,) = races_of(window)
    other = RaceGoal(GoalId("other"), RACE_DAY, Priority.C, Sport.RUNNING, 10_000, None)
    failures: list[ErrorMessage] = []
    window.plan.loader.command_failed.connect(failures.append)

    window.plan.loader.add_goal(other)  # as if the day was taken while the dialog was open
    wait_for(qtbot, lambda: bool(failures))
    wait_for(qtbot, lambda: any(b.objectName() == "errorMessage" for b in window.open_boxes))

    assert failures[0].title == "Another race is already on that day"
    assert failures[0].remedy == "Choose another day, or change the other race first."
    assert goals_of(window) == (first,)
    assert window.plan.undo_stack.count() == 0  # the stack no longer matches the store


# --- Training settings and their undo ---------------------------------------------------------


def test_training_settings_are_saved_and_taking_them_back_returns_to_not_set(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)
    assert window.plan.snapshot is not None
    assert window.plan.snapshot.availability is None

    set_training(qtbot, window, days=frozenset({0, 2, 4, 5, 6}), sessions=4, long_day=5, hours=7.5)

    snapshot = window.plan.snapshot
    assert snapshot is not None
    assert snapshot.availability is not None
    assert snapshot.availability.days == {0, 2, 4, 5, 6}
    assert snapshot.availability.sessions_per_week == 4
    assert (snapshot.availability.long_day, snapshot.availability.max_week_minutes) == (5, 450)
    assert choices_of(window).for_sport(Sport.RUNNING) == frozenset(SessionType)
    assert window.plan.actions["undo"].text() == "Undo Change training settings"  # one step

    undo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and _availability_is_none(window))

    assert choices_of(window).by_sport == ()
    redo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and not _availability_is_none(window))
    assert choices_of(window).for_sport(Sport.RUNNING) == frozenset(SessionType)


def _availability_is_none(window: MainWindow) -> bool:
    snapshot = window.plan.snapshot
    return snapshot is not None and snapshot.availability is None


def test_edits_discarded_when_closing_settings_change_nothing_and_push_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)

    window.plan.actions["training"].trigger()
    wait_for(qtbot, lambda: window.plan.training_dialog is not None)
    dialog = window.plan.training_dialog
    assert dialog is not None
    assert dialog.note_is_shown  # nothing saved yet: the form starts from suggestions
    dialog.day_checks[0].setChecked(False)
    assert window.settings_window.category_label(SettingsCategory.TRAINING_PLAN).endswith("*")
    window.settings_window.close()
    answer_unsaved(qtbot, window, "Discard")

    assert window.plan.undo_stack.count() == 0
    assert _availability_is_none(window)


def test_saving_the_suggestions_unchanged_is_a_choice_that_is_stored(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)

    window.plan.actions["training"].trigger()
    wait_for(qtbot, lambda: window.plan.training_dialog is not None)
    dialog = window.plan.training_dialog
    assert dialog is not None
    suggested = dialog.draft()
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and not _availability_is_none(window))

    assert window.plan.snapshot is not None
    assert window.plan.snapshot.availability == suggested.availability()
    assert choices_of(window) == suggested.choices


# --- The state of the plan, in words with a remedy --------------------------------------------


def test_without_training_time_the_goals_section_says_there_is_no_plan_and_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)

    wait_refusal(qtbot, window, "No training time is set.")

    (text,) = window.goals_page.notice_texts
    assert text.split("\n") == [
        "No plan",
        "No training time is set.",
        "Choose the days you can train and the hours a week in Training settings.",
    ]
    (button,) = window.goals_page.notice_buttons()
    assert button.text() == "Training settings..."
    button.click()
    wait_for(qtbot, lambda: window.plan.training_dialog is not None)
    window.plan.close_dialogs()


def test_without_goals_the_notice_offers_to_add_a_race(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)

    wait_refusal(qtbot, window, "You have no goal yet.")

    (button,) = window.goals_page.notice_buttons()
    assert button.text() == "Add race..."
    button.click()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    window.plan.close_dialogs()
    assert isinstance(window.plan.state, PlanRefusedNow)


def test_a_missing_session_type_is_said_by_the_plan_and_goes_away_when_it_is_chosen(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = ready_window(qtbot, tmp_path, settings)
    without_long = frozenset(SessionType) - {SessionType.LONG}
    set_training(qtbot, window, types=without_long)
    add_race(qtbot, window, RACE_DAY)
    wait_plan(qtbot, window, race_ids(window))

    wait_for(
        qtbot,
        lambda: any("No long session is chosen" in t for t in window.goals_page.notice_texts),
    )

    (long_text,) = [t for t in window.goals_page.notice_texts if "long session" in t]
    assert long_text.split("\n") == [
        "Warning",
        "No long session is chosen for Race A, Marathon, 9/22/26: your endurance will lag.",
        "Choose the long session in Training settings.",
    ]
    set_training(qtbot, window, types=frozenset(SessionType))
    wait_for(
        qtbot,
        lambda: not any("No long session" in t for t in window.goals_page.notice_texts),
    )


# --- The calendar, planned against done -------------------------------------------------------


def test_a_day_after_today_opens_on_the_planned_session_with_its_blocks(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    plan = wait_plan(qtbot, window, race_ids(window))
    target = next(d for d in plan.days if d.session is not None and d.session.blocks)
    page = window.calendar_page
    while page.current_month < first_of_month(target.day):
        page.next_month()

    page.select_day(target.day)
    cell = cell_of(window, target.day)

    assert page.selected_day() == target.day
    assert cell.is_future
    assert cell.plan == target
    assert target.session is not None
    detail = page.panel.detail_text
    for block in target.session.blocks:
        assert f"zone {block.zone}" in detail
    assert "Estimated load:" in detail
    assert "Plan: " in str(page.model.data(page.model.index_of(target.day)))


def test_the_race_day_is_marked_with_its_priority_and_the_phases_are_in_the_legend(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY, target="3:20:00")
    wait_plan(qtbot, window, race_ids(window))
    page = window.calendar_page
    while page.current_month < first_of_month(RACE_DAY):
        page.next_month()
    wait_for(qtbot, lambda: bool(cell_of(window, RACE_DAY).goals))

    page.select_day(RACE_DAY)

    lines = str(page.model.data(page.model.index_of(RACE_DAY))).split("\n")
    assert "Race A" in lines
    assert "Race day" in lines
    assert "Race A, Marathon, 9/22/26" in page.panel.detail_text
    assert "Race day" in page.phase_legend_texts
    ((change, remove),) = page.panel.goal_buttons()
    assert change.isEnabled()
    assert remove.isEnabled()


def test_a_goal_can_be_changed_and_removed_from_the_day_in_the_calendar_and_undone(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    (race,) = races_of(window)
    page = window.calendar_page
    while page.current_month < first_of_month(RACE_DAY):
        page.next_month()
    wait_for(qtbot, lambda: bool(cell_of(window, RACE_DAY).goals))
    page.select_day(RACE_DAY)

    ((change, remove),) = page.panel.goal_buttons()
    change.click()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.windowTitle() == "Change race"
    dialog.reject()
    remove.click()
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == ())
    wait_for(qtbot, lambda: not cell_of(window, RACE_DAY).goals)
    undo(window)
    wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == (race,))


def test_a_goal_is_added_from_a_day_of_the_calendar_with_that_day_in_the_form(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    page = window.calendar_page
    page.next_month()
    day = date(2026, 7, 18)
    page.select_day(day)

    window.plan.actions["add_race"].trigger()  # what the day menu runs
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)

    dialog = window.plan.goal_dialog
    assert dialog is not None
    assert dialog.draft().day == day
    dialog.reject()


def test_a_past_day_shows_what_was_planned_then_beside_what_was_done_and_how_it_went(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    block = Block(BlockRole.STEADY, 60, 4, 1, 150, 165, None, None)
    session = PlannedSession(Sport.RUNNING, SessionType.THRESHOLD, 60, (block,), 100.0)
    planned = PlanDay(DAWN_AND_LUNCH_DAY, Phase.BUILD, session, None, 100.0)
    frozen = FrozenDay(DAWN_AND_LUNCH_DAY - timedelta(days=9), planned)
    outcomes: list[object] = []
    window.controller.run_on_profile(
        lambda access: access.session.plans.freeze([frozen]), outcomes.append
    )
    wait_for(qtbot, lambda: bool(outcomes))
    page = window.calendar_page

    page.previous_month()  # leaving the month and coming back reads the past again
    page.next_month()
    wait_for(qtbot, lambda: cell_of(window, DAWN_AND_LUNCH_DAY).plan is not None)
    page.select_day(DAWN_AND_LUNCH_DAY)

    cell = cell_of(window, DAWN_AND_LUNCH_DAY)
    assert cell.plan == planned
    assert cell.adherence is not None
    assert cell.adherence.planned_load == 100.0
    assert cell.adherence.actual_load == pytest.approx(cell.load)
    detail = page.panel.detail_text
    assert detail.index("Threshold, 60 min") < detail.index("Done: Running")
    assert "Planned 100 TRIMPeq, done" in detail
    lines = str(page.model.data(page.model.index_of(DAWN_AND_LUNCH_DAY))).split("\n")
    assert "Plan: Threshold, 60 min" in lines
    assert "Build phase" in lines
    # a day with a plan frozen is still a day with a page: Enter opens the nutrition day
    with qtbot.waitSignal(page.day_requested, timeout=1000):
        page.request_selected_day()


# --- The PMC, the status bar ------------------------------------------------------------------


def _plan_names(window: MainWindow) -> list[str]:
    figure = window.overview_page.chart.current_figure
    return [] if figure is None else [str(t.name) for t in figure.data]


def test_the_pmc_shows_the_projected_part_apart_from_the_real_one_and_the_races_as_markers(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    assert "Fitness (CTL), plan" not in _plan_names(window)

    add_race(qtbot, window, RACE_DAY)
    wait_plan(qtbot, window, race_ids(window))
    wait_for(qtbot, lambda: "Fitness (CTL), plan" in _plan_names(window))

    figure = window.overview_page.chart.current_figure
    assert figure is not None
    names = _plan_names(window)
    assert names[:4] == ["Fitness (CTL)", "Fatigue (ATL)", "Form (TSB)", "Daily load"]
    assert names[4:] == ["Fitness (CTL), plan", "Fatigue (ATL), plan", "Form (TSB), plan", "Races"]
    dotted = [t for t in figure.data if t.name and t.name.endswith(", plan")]
    assert all(t.line.dash == "dot" for t in dotted)
    races = next(t for t in figure.data if t.name == "Races")
    assert list(races.text) == ["A"]
    assert races.x[0] == RACE_DAY


def test_taking_the_race_back_takes_the_projection_off_the_chart(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    wait_for(qtbot, lambda: "Races" in _plan_names(window))

    undo(window)

    wait_for(qtbot, lambda: "Fitness (CTL), plan" not in _plan_names(window))


def test_the_plan_proposes_the_next_threshold_test_beside_the_status_of_the_test(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    assert window.plan_retest_text == ""

    add_race(qtbot, window, RACE_DAY)
    wait_plan(qtbot, window, race_ids(window))
    wait_for(qtbot, lambda: window.plan_retest_text != "")

    # with no test yet the plan puts the test in its first weeks (ADR 015)
    assert window.plan_retest_text.startswith("Threshold test planned on ")
    wait_for(qtbot, lambda: window.retest_status_text.startswith("Threshold test: "))
    label = window.findChild(QLabel, "planRetestLabel")
    assert label is not None
    assert not label.isHidden()


def protocols_of(plan: Plan) -> set[ThresholdProtocol]:
    return {d.session.threshold_test for d in plan.days if d.session and d.session.threshold_test}


def test_the_test_the_plan_schedules_follows_the_protocol_chosen_in_the_settings(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    first = wait_plan(qtbot, window, race_ids(window))
    assert protocols_of(first) == {ThresholdProtocol.THIRTY_MINUTES}

    window.threshold_protocol_section.button(ThresholdProtocol.TWENTY_MINUTES).click()

    def rebuilt() -> bool:
        state = window.plan.state
        return isinstance(state, PlanAvailable) and protocols_of(state.plan) == {
            ThresholdProtocol.TWENTY_MINUTES
        }

    wait_for(qtbot, rebuilt)
    assert window.controller.test_protocol is ThresholdProtocol.TWENTY_MINUTES


def test_taking_the_goal_back_takes_the_proposal_off_the_status_bar(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    wait_for(qtbot, lambda: window.plan_retest_text != "")

    undo(window)

    wait_for(qtbot, lambda: window.plan_retest_text == "")


# --- Care with health data, the window's state ------------------------------------------------


def test_nothing_of_the_goals_or_the_plan_reaches_the_log(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        window = plan_window(qtbot, tmp_path, settings)
        add_race(qtbot, window, RACE_DAY, target="3:20:00")
        (race,) = races_of(window)
        wait_plan(qtbot, window, race_ids(window))
        undo(window)
        wait_for(qtbot, lambda: window.plan.is_idle and goals_of(window) == ())

    assert race.goal_id.value not in caplog.text
    assert "3:20:00" not in caplog.text
    assert "bpm" not in caplog.text


def test_switching_off_to_another_profile_forgets_the_goals_and_the_stack(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = plan_window(qtbot, tmp_path, settings)
    add_race(qtbot, window, RACE_DAY)
    assert window.plan.undo_stack.count() > 0

    window.plan.profile_changed()

    assert window.plan.undo_stack.count() == 0
    assert isinstance(window.plan.state, NoPlanYet)
    wait_idle(qtbot, window)
    assert races_of(window)[0].day == RACE_DAY  # the same profile is read again
