# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Garmin calendar in the window (phase 7): send the plan, list, delete and create again.

Garmin is the fake of `tests/fakes/` (no network, no real login). Covers mixed outcomes of a send,
cancellation, the confirmation of a deletion, recreation, and the calendar not being available.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QItemSelectionModel, QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.ports import AuthenticationRequiredError, DefinitiveSourceError
from ponos_coach.planning.calendar_use_cases import MAX_WORKOUTS_PER_SEND, StopReason
from ponos_coach.planning.domain.workouts import WORKOUT_PREFIX, workout_from_session
from ponos_coach.ui.garmin_calendar_model import (
    Deleted,
    Listed,
    Restored,
    ScopeChoice,
    Sent,
)
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.sync_model import AccountStatus
from tests.fakes.workout_calendar import InMemoryWorkoutCalendar
from tests.ui.garmin_calendar_support import (
    GatedCalendar,
    current_plan,
    select_day,
    session_days,
    summary_box,
    unlinked_window,
    wait_job,
    window_with_plan,
)
from tests.ui.garmin_support import click_button, open_box, wait_account
from tests.ui.workspace_support import TODAY, WAIT_MS, close_window, make_settings

APP_WORKOUT = f"{WORKOUT_PREFIX}Corsa leggera"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def _names_by_day(window: MainWindow) -> dict[date, str]:
    """The name of the workout of each session of the plan from today on."""
    result: dict[date, str] = {}
    for item in current_plan(window).days:
        if item.session is not None and item.day >= TODAY:
            result[item.day] = workout_from_session(item.session).name
    return result


def _open_send_dialog(qtbot: QtBot, window: MainWindow) -> None:
    window.garmin_calendar.send_plan_action.trigger()
    qtbot.waitUntil(lambda: window.garmin_calendar.send_dialog is not None, timeout=WAIT_MS)


# --- Send ------------------------------------------------------------------------------------


def test_sending_the_whole_plan_sends_the_earliest_sessions_up_to_the_limit_and_tells_how_it_went(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    wanted = _names_by_day(window)
    beyond = len(wanted) - MAX_WORKOUTS_PER_SEND
    assert beyond > 0  # a twelve-week plan has more sessions than one send takes
    earliest = {day: wanted[day] for day in sorted(wanted)[:MAX_WORKOUTS_PER_SEND]}
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None
    assert dialog.selected_choice is ScopeChoice.WHOLE_PLAN
    assert dialog.send_button.text() == f"Send workouts ({MAX_WORKOUTS_PER_SEND})"
    assert f"Sessions waiting for another send: {beyond}" in dialog.cap_note_text

    result = wait_job(qtbot, window, dialog.send_button.click)

    assert isinstance(result, Sent)
    assert len(result.outcome.sent) == MAX_WORKOUTS_PER_SEND
    assert result.outcome.deferred == beyond
    assert {w.day: w.name for w in calendar.scheduled()} == earliest
    assert all(w.name.startswith(WORKOUT_PREFIX) for w in calendar.scheduled())
    box = summary_box(qtbot, window)
    assert box.text() == "Plan sent to Garmin"
    text = box.informativeText()
    assert f"Workouts sent to Garmin: {MAX_WORKOUTS_PER_SEND}" in text
    assert f"Sessions left for a later send: {beyond}" in text
    close_window(qtbot, window)


def test_a_send_with_mixed_outcomes_says_what_was_sent_what_was_there_and_what_failed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    wanted = _names_by_day(window)
    days = sorted(wanted)
    first, second = days[0], days[1]
    calendar.add_foreign(wanted[first], first)  # already there: same name, same day
    calendar.fail("create_and_schedule", DefinitiveSourceError("refused"))  # the next one fails
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None

    result = wait_job(qtbot, window, dialog.send_button.click)

    assert isinstance(result, Sent)
    assert [w.day for w in result.outcome.already_there] == [first]
    assert [f.day for f in result.outcome.failed] == [second]
    assert len(result.outcome.sent) == MAX_WORKOUTS_PER_SEND - 1  # one failed, one was there
    box = summary_box(qtbot, window)
    assert box.text() == "The plan was not fully sent"
    text = box.informativeText()
    assert "Already on the calendar: 1" in text
    assert "Garmin refused it" in text
    assert wanted[second] in text
    close_window(qtbot, window)


def test_the_day_panel_sends_the_session_of_the_selected_day_only(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    day = session_days(current_plan(window))[3]
    select_day(window, day)
    button = window.calendar_page.panel.send_session_button
    assert button.text() == "Send this session to Garmin"
    assert button.isEnabled()

    result = wait_job(qtbot, window, button.click)

    assert isinstance(result, Sent)
    assert [w.day for w in calendar.scheduled()] == [day]
    close_window(qtbot, window)


def test_the_week_scope_sends_the_sessions_of_the_selected_week(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    days = session_days(current_plan(window))
    chosen = days[len(days) // 2]
    select_day(window, chosen)
    week = {d for d in days if d.isocalendar()[:2] == chosen.isocalendar()[:2]}
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None
    dialog.radio(ScopeChoice.WEEK).setChecked(True)
    assert dialog.send_button.text() == f"Send workouts ({len(week)})"

    result = wait_job(qtbot, window, dialog.send_button.click)

    assert isinstance(result, Sent)
    assert {w.day for w in calendar.scheduled()} == week
    close_window(qtbot, window)


def test_the_session_command_is_off_with_the_reason_on_a_day_that_has_passed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_plan(qtbot, tmp_path, settings, InMemoryWorkoutCalendar())

    select_day(window, TODAY - timedelta(days=10))

    action = window.garmin_calendar.send_session_action
    assert not action.isEnabled()
    assert action.toolTip() == "Only sessions of today or later can be sent."
    button = window.calendar_page.panel.send_session_button
    assert not button.isEnabled()
    assert button.toolTip() == action.toolTip()
    close_window(qtbot, window)


def test_the_session_command_is_off_with_the_reason_on_a_day_after_the_plan_ends(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_plan(qtbot, tmp_path, settings, InMemoryWorkoutCalendar())
    after_plan = max(session_days(current_plan(window))) + timedelta(days=1)

    select_day(window, after_plan)

    action = window.garmin_calendar.send_session_action
    assert not action.isEnabled()
    assert action.toolTip() == "The selected day has no planned session."
    close_window(qtbot, window)


def test_cancelling_a_send_keeps_what_was_sent_and_says_it_stopped(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    calendar = GatedCalendar(gate)
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None
    garmin = window.garmin_calendar

    def start_and_stop() -> None:
        dialog.send_button.click()
        assert calendar.entered.wait(WAIT_MS / 1000)  # the first workout is being created
        qtbot.waitUntil(garmin.stop_button.isEnabled, timeout=WAIT_MS)
        qtbot.waitUntil(lambda: garmin.status_text.startswith("Sending workout 1 of"))
        garmin.stop_button.click()
        gate.set()

    result = wait_job(qtbot, window, start_and_stop)

    assert isinstance(result, Sent)
    assert result.outcome.stop is StopReason.CANCELLED
    assert len(result.outcome.sent) == 1
    assert len(calendar.scheduled()) == 1
    assert summary_box(qtbot, window).text() == "Sending stopped"
    close_window(qtbot, window)


# --- The list, deletion and recreation ---------------------------------------------------------


def _open_list(qtbot: QtBot, window: MainWindow, expected: int) -> None:
    window.garmin_calendar.browse_action.trigger()
    qtbot.waitUntil(
        lambda: (
            window.garmin_calendar.calendar_dialog is not None
            and window.garmin_calendar.calendar_dialog.model.rowCount() == expected
        ),
        timeout=WAIT_MS,
    )


def _select_rows(window: MainWindow, rows: list[int]) -> None:
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    selection = dialog.table.selectionModel()
    selection.clearSelection()
    for row in rows:
        selection.select(
            dialog.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def _calendar_with_three() -> InMemoryWorkoutCalendar:
    calendar = InMemoryWorkoutCalendar()
    calendar.add_foreign("Tempo di prova", TODAY)
    calendar.add_foreign("Fondo di prova", TODAY + timedelta(days=1))
    calendar.add_foreign(APP_WORKOUT, TODAY + timedelta(days=2))
    return calendar


def test_the_list_shows_the_future_workouts_with_who_created_them(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = _calendar_with_three()
    calendar.add_foreign("Vecchio di prova", TODAY - timedelta(days=20))  # past: not listed
    window = window_with_plan(qtbot, tmp_path, settings, calendar)

    _open_list(qtbot, window, 3)

    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    model = dialog.table.model()
    rows = [[model.index(r, c).data() for c in range(3)] for r in range(model.rowCount())]
    assert [row[1] for row in rows] == ["Tempo di prova", "Fondo di prova", APP_WORKOUT]
    assert [row[2] for row in rows] == ["Other", "Other", "Ponos Coach"]
    assert dialog.status_text == "Workouts from today on: 3"
    assert not dialog.delete_button.isEnabled()
    assert dialog.delete_button.toolTip() == "Select the workouts to delete first."
    close_window(qtbot, window)


def test_deleting_asks_with_the_list_and_the_verb_then_keeps_a_copy_and_can_create_them_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    planned = _names_by_day(window)  # the app's workout is one the plan still has
    planned_day = min(planned)
    app_workout = planned[planned_day]
    calendar.add_foreign("Tempo di prova", TODAY)
    calendar.add_foreign(app_workout, planned_day)
    calendar.add_foreign("Fondo di prova", planned_day + timedelta(days=1))
    _open_list(qtbot, window, 3)
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    _select_rows(window, [0, 1])
    assert dialog.delete_button.isEnabled()

    dialog.delete_button.click()

    box = open_box(qtbot, window, "deleteWorkoutsConfirmation")
    assert box.text() == "Delete the selected workouts from the Garmin calendar? (2)"
    assert "Tempo di prova" in box.informativeText()
    assert app_workout in box.informativeText()
    assert "Fondo di prova" not in box.informativeText()
    assert sorted(b.text() for b in box.buttons() if b.text() != "Show Details...") == [
        "Delete workouts (2)",
        "Keep them",
    ]
    assert box.defaultButton().text() == "Keep them"  # the safe button
    assert len(calendar.scheduled()) == 3  # nothing happens before the answer

    deleted = wait_job(qtbot, window, lambda: click_button(box, "Delete workouts (2)"))

    assert isinstance(deleted, Deleted)
    assert [w.name for w in deleted.outcome.deleted] == ["Tempo di prova", app_workout]
    assert [w.name for w in calendar.scheduled()] == ["Fondo di prova"]
    assert (
        "Workouts deleted from the Garmin calendar: 2"
        in summary_box(qtbot, window).informativeText()
    )
    # the dialog reads the calendar again by itself and now offers the copies
    qtbot.waitUntil(
        lambda: dialog.recreate_button.text() == "Recreate deleted (2)", timeout=WAIT_MS
    )
    assert dialog.model.rowCount() == 1
    qtbot.waitUntil(dialog.recreate_button.isEnabled, timeout=WAIT_MS)

    restored = wait_job(qtbot, window, dialog.recreate_button.click)

    assert isinstance(restored, Restored)
    assert len(restored.outcome.restored) == 2
    assert sorted(w.name for w in calendar.scheduled()) == sorted(
        ["Tempo di prova", "Fondo di prova", app_workout]
    )
    qtbot.waitUntil(
        lambda: dialog.recreate_button.text() == "Recreate deleted (0)", timeout=WAIT_MS
    )
    assert not dialog.recreate_button.isEnabled()
    close_window(qtbot, window)


def test_declining_the_confirmation_deletes_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = _calendar_with_three()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    _open_list(qtbot, window, 3)
    _select_rows(window, [1])
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    dialog.delete_button.click()
    box = open_box(qtbot, window, "deleteWorkoutsConfirmation")
    assert box.text() == "Delete the selected workouts from the Garmin calendar? (1)"

    click_button(box, "Keep them")

    assert len(calendar.scheduled()) == 3
    assert not window.controller.sync.is_calendar_busy
    close_window(qtbot, window)


def test_a_deletion_whose_copy_cannot_be_read_keeps_the_workout_and_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = _calendar_with_three()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    _open_list(qtbot, window, 3)
    _select_rows(window, [0, 1])
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    dialog.delete_button.click()
    box = open_box(qtbot, window, "deleteWorkoutsConfirmation")
    calendar.fail("export_definition", DefinitiveSourceError("not found"))

    deleted = wait_job(qtbot, window, lambda: click_button(box, "Delete workouts (2)"))

    assert isinstance(deleted, Deleted)
    assert len(deleted.outcome.failed) == 1
    assert len(calendar.scheduled()) == 2  # the one without a copy is still there
    text = summary_box(qtbot, window).informativeText()
    assert "it could not be copied first, so it was kept" in text
    close_window(qtbot, window)


def test_listing_again_works(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> None:
    calendar = _calendar_with_three()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    _open_list(qtbot, window, 3)
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    calendar.add_foreign("Nuovo di prova", TODAY)

    result = wait_job(qtbot, window, dialog.refresh_button.click)

    assert isinstance(result, Listed)
    assert len(result.outcome.workouts) == 4
    close_window(qtbot, window)


# --- Calendar not available -----------------------------------------------------------------------


def test_without_an_account_the_commands_are_off_and_say_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = unlinked_window(qtbot, tmp_path, settings, InMemoryWorkoutCalendar())

    garmin = window.garmin_calendar
    for action in (garmin.send_plan_action, garmin.browse_action, garmin.recreate_action):
        assert not action.isEnabled()
        assert action.toolTip() == "Connect a Garmin account first."
    assert not garmin.stop_action.isEnabled()
    close_window(qtbot, window)


def test_an_adapter_that_cannot_do_workouts_is_told_once_and_the_commands_say_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_plan(qtbot, tmp_path, settings, None)
    garmin = window.garmin_calendar
    assert garmin.browse_action.isEnabled()  # it cannot be known before trying

    wait_job(qtbot, window, garmin.browse_action.trigger)

    box = summary_box(qtbot, window)
    assert box.text() == "The Garmin task did not start"
    assert "This Garmin connection cannot manage workouts." in box.informativeText()
    for action in (garmin.send_plan_action, garmin.browse_action, garmin.recreate_action):
        assert not action.isEnabled()
        assert action.toolTip() == "This Garmin connection cannot manage workouts."
    assert not window.calendar_page.panel.send_session_button.isEnabled()
    close_window(qtbot, window)


def test_when_garmin_asks_to_connect_again_the_summary_offers_it_and_the_account_says_so(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    calendar.fail("list_scheduled", AuthenticationRequiredError("expired"))

    result = wait_job(qtbot, window, window.garmin_calendar.browse_action.trigger)

    assert isinstance(result, Listed)
    wait_account(qtbot, window, AccountStatus.NEEDS_RECONNECT)
    box = summary_box(qtbot, window)
    assert "Connect account..." in [b.text() for b in box.buttons()]
    assert window.garmin_calendar.browse_action.toolTip() == "Garmin asks you to connect again."
    close_window(qtbot, window)


def test_a_calendar_job_does_not_put_workouts_or_plans_in_the_log(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    names = list(_names_by_day(window).values())
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None

    with caplog.at_level(logging.DEBUG):
        wait_job(qtbot, window, dialog.send_button.click)

    assert not any(name in caplog.text for name in names)
    close_window(qtbot, window)


def test_another_garmin_task_is_not_started_while_a_calendar_task_runs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    calendar = GatedCalendar(gate)
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    _open_send_dialog(qtbot, window)
    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None
    dialog.send_button.click()
    assert calendar.entered.wait(WAIT_MS / 1000)

    garmin = window.garmin_calendar
    qtbot.waitUntil(lambda: not garmin.send_plan_action.isEnabled(), timeout=WAIT_MS)
    assert (
        garmin.send_plan_action.toolTip()
        == "Another Garmin task is running; wait for it or stop it."
    )
    assert not window.garmin.sync_action.isEnabled()
    assert garmin.stop_action.isEnabled()

    with qtbot.waitSignal(window.controller.sync.calendar_job_finished, timeout=WAIT_MS):
        garmin.stop_button.click()
        gate.set()
    close_window(qtbot, window)


def test_creating_again_after_the_plan_changed_says_so_and_offers_to_delete_the_old_sessions(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    calendar = InMemoryWorkoutCalendar()
    window = window_with_plan(qtbot, tmp_path, settings, calendar)
    stale = f"{WORKOUT_PREFIX}Old session"
    gone = f"{WORKOUT_PREFIX}Older session"
    calendar.add_foreign(stale, TODAY + timedelta(days=2))
    calendar.add_foreign(gone, TODAY + timedelta(days=3))
    _open_list(qtbot, window, 2)
    dialog = window.garmin_calendar.calendar_dialog
    assert dialog is not None
    _select_rows(window, [1])
    dialog.delete_button.click()
    box = open_box(qtbot, window, "deleteWorkoutsConfirmation")
    wait_job(qtbot, window, lambda: click_button(box, "Delete workouts (1)"))
    summary_box(qtbot, window).close()  # the summary of the deletion
    qtbot.waitUntil(
        lambda: dialog.recreate_button.text() == "Recreate deleted (1)", timeout=WAIT_MS
    )
    qtbot.waitUntil(dialog.recreate_button.isEnabled, timeout=WAIT_MS)

    restored = wait_job(qtbot, window, dialog.recreate_button.click)

    assert isinstance(restored, Restored) and restored.outcome.restored == ()
    assert "the plan no longer has this session" in summary_box(qtbot, window).informativeText()
    offer = open_box(qtbot, window, "deleteWorkoutsConfirmation")
    assert "The plan has changed" in offer.text()
    assert stale in offer.informativeText() and gone not in offer.informativeText()
    assert sorted(b.text() for b in offer.buttons() if b.text() != "Show Details...") == [
        "Delete workouts (1)",
        "Keep them",
    ]
    click_button(offer, "Keep them")
    assert [w.name for w in calendar.scheduled()] == [stale]  # nothing is deleted unasked
    close_window(qtbot, window)


def test_the_send_dialog_says_the_workout_format_is_not_verified_with_a_real_account(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_plan(qtbot, tmp_path, settings, InMemoryWorkoutCalendar())

    _open_send_dialog(qtbot, window)

    dialog = window.garmin_calendar.send_dialog
    assert dialog is not None
    assert "has not been checked yet with a real Garmin account" in dialog.format_note_text
    close_window(qtbot, window)
