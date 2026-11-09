# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Injuries and illnesses in the window (T-INF with the interface, T-UI-03).

From the Composition Root, with a made-up athlete and activities in June 2026 (today is 30 June):
the questionnaire, the check-in and the other commands go through the dialogs and the undo stack,
the data thread writes with the real use cases, and the plan is rebuilt by the real engine after
each command. A warning sign stops the plan, shows an invitation to a doctor and never a diagnosis.
Everything that is health data stays out of the log.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QModelIndex, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QMenu,
    QWizard,
)
from pytestqt.qtbot import QtBot

from ponos_coach.injury.domain import (
    BodyZone,
    HaltReason,
    IllnessKind,
    Injury,
    Step,
    WarningSign,
)
from ponos_coach.planning.domain import (
    NoSessionPossible,
    PlanAdapted,
    PlanHalted,
)
from ponos_coach.ui.birth_date_edit import from_qdate
from ponos_coach.ui.calendar_texts import cell_text, day_detail_text
from ponos_coach.ui.injury_dialogs import CheckInDialog
from ponos_coach.ui.injury_presentation import HealthNotice, NoticeKind, ProblemKind, ProblemPhase
from ponos_coach.ui.injury_texts import (
    doctor_invitation,
    halt_texts,
    notice_texts,
    plan_warning_texts,
    problem_title,
    sign_text,
)
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.plan_presentation import PlanAvailable
from tests.ui.goals_support import wait_for
from tests.ui.injury_support import (
    check_in,
    checkin,
    injury_window,
    redo,
    report_illness,
    report_injury,
    seed_injury,
    select_record,
    stored,
    summary_of,
    undo,
    wait_injuries,
    wait_plan_state,
)
from tests.ui.nutrition_support import PROFILE, cell_of
from tests.ui.workspace_support import TODAY, compose, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def stored_injury(window: MainWindow) -> Injury:
    (problem,) = [p for p in stored(window) if isinstance(p, Injury)]
    return problem


# --- 1. The section, the menu, the commands with their reasons ---------------------------------


def test_the_history_opens_from_the_menu_and_the_commands_have_their_reasons(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    window.injury_history_action.trigger()

    assert window.injury_history_window.isVisible()
    assert window.injury_history_window.windowTitle() == "Injury history"
    assert window.injuries_page.empty_text.startswith("No injuries or illnesses.")
    actions = window.injuries.actions
    assert actions["report_injury"].isEnabled()
    assert actions["report_illness"].isEnabled()
    for name, reason in (
        ("check_in", "Select a day with an open injury in the Calendar first."),
        ("review", "No warning sign is waiting for a doctor's visit."),
        ("close", "Select a day with an open injury or illness in the Calendar first."),
    ):
        assert not actions[name].isEnabled()
        assert actions[name].toolTip() == reason
    assert not actions["undo"].isEnabled()
    assert actions["undo"].toolTip() == "There is nothing to take back."


def test_the_injuries_menu_lists_every_command_with_its_shortcut(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Injuries")

    assert isinstance(menu, QMenu)
    assert [(a.text(), a.shortcut().toString()) for a in menu.actions() if not a.isSeparator()] == [
        ("Add injury...", "Ctrl+J"),
        ("Add illness...", "Ctrl+Shift+J"),
        ("Daily check-in...", "Ctrl+K"),
        ("Medical review done", "Ctrl+Shift+V"),
        ("Close injury or illness...", ""),
        ("Injury history...", "Ctrl+H"),
        ("Undo", ""),  # like the plan: no global shortcut
        ("Redo", ""),
    ]


def test_without_a_profile_the_injury_commands_are_off_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    for name in ("report_injury", "report_illness", "check_in", "review", "close"):
        action = window.injuries.actions[name]
        assert not action.isEnabled()
        assert action.toolTip() == "Create or open a profile first."
    assert window.injury_status_text == ""


def test_the_history_is_usable_from_the_keyboard(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    window.injury_history_action.trigger()
    page = window.injuries_page
    page.select_problem(summary_of(window).problem_id)

    QTest.keyClick(page.table, Qt.Key.Key_Return)

    wait_for(qtbot, lambda: window.injuries.checkin_dialog is not None)
    # Every control of the dialog has a name for a screen reader, and the table has one too.
    dialog = window.injuries.checkin_dialog
    assert dialog is not None
    assert page.table.accessibleName() == "Injuries and illnesses"
    for spin in (dialog.during_spin, dialog.morning_spin):
        assert spin.accessibleName()
    for button in dialog.findChildren(QCheckBox):
        assert button.text()
    dialog.reject()


# --- 2. The questionnaire ------------------------------------------------------------------------


def test_an_injury_reported_in_the_questionnaire_is_in_the_table_with_its_state(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_injury(qtbot, window, rest=1, walking=2, running=4, morning=2)

    injury = stored_injury(window)
    assert injury.opened_on == TODAY
    assert injury.questionnaire.zone is BodyZone.SHIN
    assert injury.questionnaire.pain.running is not None
    assert injury.questionnaire.pain.running.value == 4
    model = window.injuries_page.proxy
    assert str(model.index(0, 0).data()) == "Shin, left"
    assert str(model.index(0, 1).data()) == "6/30/26"
    assert str(model.index(0, 2).data()) == "Gentle activation"
    assert str(model.index(0, 3).data()) == "Due today"
    window.injuries_page.select_problem(summary_of(window).problem_id)
    detail = window.injuries_page.detail_text
    assert "Current step: Gentle activation." in detail
    assert "Allowed now: Light movement" in detail
    assert "Good days in a row: 0 of 3 for the next step." in detail
    assert "Today's check-in is missing." in detail


def test_the_questionnaire_stops_mistakes_before_they_happen(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    window.injuries.actions["report_injury"].trigger()
    wait_for(qtbot, lambda: window.injuries.wizard is not None)
    wizard = window.injuries.wizard
    assert wizard is not None

    # The pain began today at the latest; the answers cannot leave the 0 to 10 scale.
    assert from_qdate(wizard.onset_day_edit.maximumDate()) == TODAY
    wizard.rest_spin.setValue(99)
    assert wizard.rest_spin.value() == 10
    wizard.running_spin.setValue(-7)
    assert wizard.running_spin.value() == -1
    assert wizard.running_spin.text() == "I cannot run, or I have not tried"
    # Strong rest pain is a warning sign: the invitation shows with no box ticked.
    assert wizard.signs_group.invitation_shown()
    wizard.rest_spin.setValue(1)
    assert not wizard.signs_group.invitation_shown()
    # Notes that cannot be saved keep the last button off, with the reason in words.
    wizard.next()
    wizard.next()
    wizard.next()
    wizard.notes_edit.setPlainText("x" * 2001)
    assert not wizard.button(QWizard.WizardButton.FinishButton).isEnabled()
    problem = wizard.notes_page.findChild(QLabel, "injuryNotesProblem")
    assert problem is not None
    assert "too long" in problem.text()
    wizard.notes_edit.setPlainText("A note.")
    assert wizard.button(QWizard.WizardButton.FinishButton).text() == "Report injury"
    assert wizard.button(QWizard.WizardButton.FinishButton).isEnabled()
    wizard.reject()


def test_cancelling_the_questionnaire_changes_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    window.injuries.actions["report_injury"].trigger()
    wait_for(qtbot, lambda: window.injuries.wizard is not None)
    wizard = window.injuries.wizard
    assert wizard is not None
    wizard.rest_spin.setValue(3)

    wizard.reject()

    wait_for(qtbot, lambda: window.injuries.wizard is None)
    assert window.injuries.undo_stack.count() == 0
    assert stored(window) == ()


def test_the_doctors_notes_are_saved_and_the_zone_and_the_side_are_the_ones_chosen(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_injury(qtbot, window, notes="Rest for two weeks.", zone_index=5)

    injury = stored_injury(window)
    assert injury.questionnaire.doctor_notes == "Rest for two weeks."
    assert injury.questionnaire.zone is BodyZone.KNEE


# --- 3. Warning signs and illness: a doctor, never a diagnosis ---------------------------------


def test_a_warning_sign_stops_the_plan_and_invites_a_doctor_without_blocking_anything(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_injury(qtbot, window, signs={WarningSign.NIGHT_PAIN})

    plan = wait_plan_state(qtbot, window, PlanHalted)
    (halt,) = [w for w in plan.warnings if isinstance(w, PlanHalted)]
    assert halt.reason is HaltReason.ZONE_SIGN
    assert all(day.session is None for day in plan.days)
    # The notice sits on the history and on the goals window, with the words and the remedy.
    notices = window.injuries_page.notice_texts
    assert notices[0].startswith("See a doctor\n")
    assert "deserves a doctor's visit" in notices[0]
    assert "Medical review done" in notices[0]
    assert any("no sessions" in text for text in window.goals_page.notice_texts)
    assert "Show the injuries" in [b.text() for b in window.goals_page.notice_buttons()]
    # The status bar says it, and a message that does not block opens once.
    assert window.injury_status_text == "Injuries: See a doctor, Check-in due today (1)"
    alert = window.injuries.alert
    assert alert is not None
    assert not alert.isModal()
    assert alert.windowTitle() == "Please see a doctor"
    assert "cannot tell what it means" in alert.text()
    # Nothing else is blocked: the other commands work and the window answers.
    assert window.injuries.actions["report_illness"].isEnabled()
    select_record(qtbot, window)
    assert window.injuries.actions["review"].isEnabled()


def test_the_texts_never_diagnose_or_say_healed() -> None:
    texts = [sign_text(sign) for sign in WarningSign]
    texts += [doctor_invitation()]
    for reason in HaltReason:
        texts += list(halt_texts(reason))
        texts += list(plan_warning_texts(PlanHalted(reason, ("x",))))
    texts += list(notice_texts(HealthNotice(NoticeKind.PLAN, warning=NoSessionPossible(("x",)))))
    joined = " ".join(texts).lower()
    for word in ("diagnos", "heal", "cured", "fracture", "thrombosis", "infarct", "tendinitis"):
        assert word not in joined


def test_a_medical_review_starts_the_path_again_and_can_be_taken_back_and_done_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window, signs={WarningSign.SWELLING_OR_DEFORMITY})
    wait_plan_state(qtbot, window, PlanHalted)
    before = stored(window)

    select_record(qtbot, window)

    window.injuries.actions["review"].trigger()
    wait_injuries(qtbot, window)
    plan = wait_plan_state(qtbot, window, PlanHalted, present=False)

    assert summary_of(window).phase is ProblemPhase.ACTIVE
    assert summary_of(window).step is Step.ACTIVATION
    assert window.injury_status_text == "Injuries: Check-in due today (1)"
    assert not any("See a doctor" in text for text in window.injuries_page.notice_texts)
    assert any(isinstance(w, PlanAdapted | NoSessionPossible) for w in plan.warnings)
    after = stored(window)
    assert after != before
    assert window.injuries.undo_stack.undoText() == "Medical review done"

    undo(window)
    wait_plan_state(qtbot, window, PlanHalted)
    assert stored(window) == before
    assert summary_of(window).phase is ProblemPhase.HALTED
    redo(window)
    wait_plan_state(qtbot, window, PlanHalted, present=False)
    assert stored(window) == after


def test_the_review_is_refused_with_a_reason_when_no_sign_waits(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)

    review = window.injuries.actions["review"]

    assert not review.isEnabled()
    assert review.toolTip() == "No warning sign is waiting for a doctor's visit."


def test_strong_rest_pain_is_a_warning_sign_too(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_injury(qtbot, window, rest=7)

    assert summary_of(window).phase is ProblemPhase.HALTED
    assert WarningSign.STRONG_REST_PAIN in summary_of(window).alarms
    wait_plan_state(qtbot, window, PlanHalted)


def test_an_illness_with_fever_stops_the_plan_until_the_user_feels_well(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_illness(qtbot, window, IllnessKind.SYSTEMIC)

    plan = wait_plan_state(qtbot, window, PlanHalted)
    (halt,) = [w for w in plan.warnings if isinstance(w, PlanHalted)]
    assert halt.reason is HaltReason.ILLNESS
    notice = window.injuries_page.notice_texts[0]
    assert notice.startswith("See a doctor\n")
    assert "no sessions until you feel well" in notice
    assert window.injury_status_text == "Injuries: See a doctor"
    assert window.injuries.alert is not None
    # One illness at a time: the command says why it is off.
    report = window.injuries.actions["report_illness"]
    assert not report.isEnabled()
    assert report.toolTip() == "An illness is already open: close it first."

    select_record(qtbot, window)

    window.injuries.actions["close"].trigger()
    wait_plan_state(qtbot, window, PlanHalted, present=False)
    assert summary_of(window).phase is ProblemPhase.CLOSED
    assert window.injury_status_text == ""
    assert window.injuries.actions["report_illness"].isEnabled()


def test_an_illness_above_the_neck_only_trims_the_plan(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)

    report_illness(qtbot, window, IllnessKind.NECK_UP)

    plan = wait_plan_state(qtbot, window, PlanAdapted)
    assert not any(isinstance(w, PlanHalted) for w in plan.warnings)
    assert window.injury_status_text == ""
    assert window.injuries.alert is None
    assert summary_of(window).kind is ProblemKind.ILLNESS
    assert str(window.injuries_page.proxy.index(0, 2).data()) == "Ongoing"


def test_the_illness_dialog_says_what_each_choice_does(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    window.injuries.actions["report_illness"].trigger()
    wait_for(qtbot, lambda: window.injuries.illness_dialog is not None)
    dialog = window.injuries.illness_dialog
    assert dialog is not None

    assert from_qdate(dialog.date_edit.maximumDate()) == TODAY
    dialog.radios[IllnessKind.SYSTEMIC].setChecked(True)
    hint = dialog.findChild(QLabel, "illnessHint")
    assert hint is not None
    assert hint.text() == (
        "The plan has no sessions until you say you feel well. A doctor's visit is advised."
    )
    dialog.radios[IllnessKind.NECK_UP].setChecked(True)
    assert hint.text() == "The plan keeps only a short easy session, with no hard work."
    dialog.reject()


# --- 4. The check-in: up, down, the same day again --------------------------------------------


def test_a_good_check_in_makes_the_path_go_up_one_step(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(
        qtbot,
        window,
        2,
        [
            checkin(TODAY.replace(day=28), during=1, morning=2),
            checkin(TODAY.replace(day=29), during=1, morning=2),
        ],
    )
    assert summary_of(window).step is Step.ACTIVATION
    assert summary_of(window).good_days == 2

    check_in(qtbot, window, during=1, morning=2)

    item = summary_of(window)
    assert item.step is Step.WALKING
    window.injuries_page.select_problem(item.problem_id)
    detail = window.injuries_page.detail_text
    assert "Current step: Walking." in detail
    assert "The last check-in was good enough to go up one step." in detail
    assert "Today's check-in is done." in detail
    assert window.injury_status_text == ""


def test_a_worse_check_in_makes_the_path_go_back_one_step(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(
        qtbot,
        window,
        3,
        [checkin(TODAY.replace(day=d), during=1, morning=2) for d in (27, 28, 29)],
    )
    assert summary_of(window).step is Step.WALKING

    check_in(qtbot, window, during=6, morning=2)

    item = summary_of(window)
    assert item.step is Step.ACTIVATION
    window.injuries_page.select_problem(item.problem_id)
    assert "The last check-in was worse than before: one step back." in (
        window.injuries_page.detail_text
    )


def test_pain_a_little_too_high_holds_the_step(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(qtbot, window, 3, [checkin(TODAY.replace(day=29), during=1, morning=2)])

    check_in(qtbot, window, during=4, morning=2)

    item = summary_of(window)
    assert (item.step, item.good_days) == (Step.ACTIVATION, 0)
    window.injuries_page.select_problem(item.problem_id)
    assert "asked a little too much" in window.injuries_page.detail_text


def test_a_check_in_with_a_warning_sign_halts_the_path_and_the_plan(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(qtbot, window, 2, [checkin(TODAY.replace(day=28), during=1, morning=2)])
    wait_plan_state(qtbot, window, PlanHalted, present=False)

    check_in(qtbot, window, during=1, morning=2, signs={WarningSign.NUMBNESS_OR_WEAKNESS})

    assert summary_of(window).phase is ProblemPhase.HALTED
    wait_plan_state(qtbot, window, PlanHalted)
    assert window.injuries.alert is not None


def test_the_check_in_of_the_same_day_replaces_the_one_before_and_shows_it_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    check_in(qtbot, window, during=2, morning=1)

    select_record(qtbot, window)

    window.injuries.actions["check_in"].trigger()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is not None)
    dialog = window.injuries.checkin_dialog
    assert dialog is not None
    assert (dialog.during_spin.value(), dialog.morning_spin.value()) == (2, 1)
    dialog.morning_spin.setValue(0)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is None and window.injuries.is_idle)

    (checkin_of_today,) = stored_injury(window).checkins
    assert checkin_of_today.pain_morning.value == 0


def test_the_check_in_dialog_edits_a_copy_and_cancel_changes_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    before = stored(window)
    select_record(qtbot, window)
    window.injuries.actions["check_in"].trigger()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is not None)
    dialog = window.injuries.checkin_dialog
    assert dialog is not None
    assert dialog.during_spin.text() == "No activity today"
    dialog.morning_spin.setValue(9)

    dialog.reject()

    wait_for(qtbot, lambda: window.injuries.checkin_dialog is None)
    assert stored(window) == before
    assert window.injuries.undo_stack.count() == 1  # only the report


def test_a_sign_ticked_in_the_check_in_shows_the_invitation_at_once(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    select_record(qtbot, window)
    window.injuries.actions["check_in"].trigger()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is not None)
    dialog = window.injuries.checkin_dialog
    assert dialog is not None

    assert not dialog.signs_group.invitation_shown()
    dialog.signs_group.checks[WarningSign.CHEST_PAIN].setChecked(True)

    assert dialog.signs_group.invitation_shown()
    dialog.reject()


# --- 5. Undo and redo that put back exactly what was --------------------------------------------


def test_taking_back_a_check_in_restores_the_exact_record_and_redo_the_same_one(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(
        qtbot,
        window,
        2,
        [
            checkin(TODAY.replace(day=28), during=1, morning=2),
            checkin(TODAY.replace(day=29), during=1, morning=2),
        ],
    )
    before = stored(window)

    check_in(qtbot, window, during=1, morning=2)
    after = stored(window)
    assert after != before

    undo(window)
    wait_for(qtbot, lambda: window.injuries.is_idle and stored(window) == before)
    assert summary_of(window).step is Step.ACTIVATION
    redo(window)
    wait_for(qtbot, lambda: window.injuries.is_idle and stored(window) == after)
    assert summary_of(window).step is Step.WALKING


def test_taking_back_a_report_removes_the_record_and_redo_puts_the_same_one_back(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window, signs={WarningSign.NIGHT_PAIN}, notes="A note.")
    reported = stored(window)
    wait_plan_state(qtbot, window, PlanHalted)
    assert window.injuries.undo_stack.undoText() == "Report injury"

    undo(window)
    wait_injuries(qtbot, window, 0)
    wait_plan_state(qtbot, window, PlanHalted, present=False)
    assert stored(window) == ()
    assert window.injury_status_text == ""
    assert window.injuries_page.empty_text.startswith("No injuries or illnesses.")

    redo(window)
    wait_injuries(qtbot, window, 1)
    assert stored(window) == reported
    wait_plan_state(qtbot, window, PlanHalted)


def test_closing_an_injury_can_be_taken_back_and_the_plan_follows(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(qtbot, window, 2, [checkin(TODAY.replace(day=28), during=1, morning=2)])
    wait_plan_state(qtbot, window, PlanAdapted)
    before = stored(window)

    select_record(qtbot, window)

    window.injuries.actions["close"].trigger()
    wait_plan_state(qtbot, window, PlanAdapted, present=False)

    assert summary_of(window).phase is ProblemPhase.CLOSED
    assert str(window.injuries_page.proxy.index(0, 2).data()) == "Closed"
    state = window.plan.state
    assert isinstance(state, PlanAvailable)
    assert state.plan.inputs.after_injury  # the weekly ramp stays lower for a while
    assert not window.injuries.actions["close"].isEnabled()

    undo(window)
    wait_plan_state(qtbot, window, PlanAdapted)
    assert stored(window) == before
    assert summary_of(window).phase is ProblemPhase.ACTIVE


def test_undo_and_redo_say_what_they_take_back_at_the_end_of_the_menu(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    check_in(qtbot, window, during=1, morning=1)

    actions = window.injuries.actions

    assert actions["undo"].text() == "Undo Save check-in"
    undo(window)
    wait_injuries(qtbot, window)
    assert actions["redo"].text() == "Redo Save check-in"
    assert actions["undo"].text() == "Undo Report injury"
    assert not actions["undo"].shortcut().toString()


# --- 6. Which injury a command acts on ----------------------------------------------------------


def checkin_dialog_of(window: MainWindow) -> CheckInDialog | None:
    """The open check-in dialog, read through a function (earlier checks narrow the type)."""
    return window.injuries.checkin_dialog


def test_with_two_injuries_on_a_day_the_command_asks_which(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window, zone_index=4)
    report_injury(qtbot, window, zone_index=5)
    check_in_action = window.injuries.actions["check_in"]
    window.calendar_page.table.setCurrentIndex(QModelIndex())  # no day selected
    check_in_action.trigger()

    assert checkin_dialog_of(window) is None
    assert not check_in_action.isEnabled()
    assert check_in_action.toolTip() == "Select a day with an open injury in the Calendar first."
    assert window.injury_status_text == "Injuries: Check-in due today (2)"

    select_record(qtbot, window)
    assert check_in_action.isEnabled()
    check_in_action.trigger()
    menu = window.findChild(QMenu, "choiceMenu")
    assert menu is not None
    assert sorted(a.text() for a in menu.actions()) == sorted(
        problem_title(item) for item in window.injuries.summaries
    )
    menu.actions()[0].trigger()
    wait_for(qtbot, lambda: checkin_dialog_of(window) is not None)
    dialog = checkin_dialog_of(window)
    assert dialog is not None
    dialog.reject()


# --- 7. The calendar marks the days, in words -----------------------------------------------------


def test_the_calendar_marks_the_days_of_an_injury_with_words_and_not_only_colour(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    seed_injury(qtbot, window, 5, [checkin(TODAY.replace(day=26), during=1, morning=2)])
    wait_for(qtbot, lambda: cell_of(window, TODAY).problems != ())

    for day in range(25, 31):
        cell = cell_of(window, TODAY.replace(day=day))
        assert "Injury: Shin" in cell_text(cell)
        assert "Injury: Shin" in day_detail_text(cell)
    assert cell_of(window, TODAY.replace(day=24)).problems == ()
    assert "Injury" not in cell_text(cell_of(window, TODAY.replace(day=24)))


def test_the_calendar_marks_an_illness_and_stops_at_the_day_it_is_over(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_illness(qtbot, window, IllnessKind.NECK_UP)
    assert "Illness" in cell_text(cell_of(window, TODAY))

    select_record(qtbot, window)

    window.injuries.actions["close"].trigger()
    wait_injuries(qtbot, window)

    # Closed today: today is still a day of the illness, tomorrow (not shown as a past day) is not.
    assert "Illness" in cell_text(cell_of(window, TODAY))
    assert cell_of(window, TODAY.replace(day=29)).problems == ()


def test_taking_back_the_report_unmarks_the_calendar(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window)
    assert cell_of(window, TODAY).problems != ()

    undo(window)
    wait_injuries(qtbot, window, 0)

    assert cell_of(window, TODAY).problems == ()


# --- 8. The window's state ---------------------------------------------------------------------


def test_switching_to_another_profile_forgets_the_injuries_and_the_stack(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    report_injury(qtbot, window, signs={WarningSign.NIGHT_PAIN})
    assert window.injuries.undo_stack.count() > 0
    assert window.injury_status_text

    window.injuries.profile_changed()

    assert window.injuries.undo_stack.count() == 0
    assert window.injury_status_text == ""
    assert window.injuries.alert is None
    wait_injuries(qtbot, window, 1)  # read again from the store


def test_nothing_of_the_injuries_reaches_the_log(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    note_text = "Private note of the doctor."
    with caplog.at_level(logging.DEBUG):
        window = injury_window(qtbot, tmp_path, settings)
        report_injury(
            qtbot, window, signs={WarningSign.NIGHT_PAIN}, notes=note_text, rest=7, zone_index=5
        )
        injury_id = summary_of(window).problem_id
        check_in(qtbot, window, during=8, morning=6, signs={WarningSign.CHEST_PAIN})
        select_record(qtbot, window)
        window.injuries.actions["review"].trigger()
        wait_injuries(qtbot, window)
        undo(window)
        wait_injuries(qtbot, window)
        report_illness(qtbot, window, IllnessKind.SYSTEMIC)
        select_record(qtbot, window)
        window.injuries.actions["close"].trigger()
        wait_injuries(qtbot, window)

    text = caplog.text.lower()
    assert note_text.lower() not in text
    assert injury_id not in text
    for word in ("knee", "night", "chest", "pain", "injur", "illness", "fever", "doctor"):
        assert word not in text
    assert PROFILE.lower() not in text


def test_the_combo_of_zones_offers_every_zone_in_the_users_words(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = injury_window(qtbot, tmp_path, settings)
    window.injuries.actions["report_injury"].trigger()
    wait_for(qtbot, lambda: window.injuries.wizard is not None)
    wizard = window.injuries.wizard
    assert wizard is not None
    combo = wizard.findChild(QComboBox, "injuryZone")
    assert combo is not None

    assert [combo.itemText(i) for i in range(combo.count())] == [
        "Foot or heel",
        "Ankle",
        "Achilles tendon",
        "Calf",
        "Shin",
        "Knee",
        "Thigh",
        "Hip or groin",
        "Lower back",
        "Upper body (shoulder, arm, neck)",
    ]
    wizard.reject()
