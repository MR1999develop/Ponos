# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 4: the threshold test in the real window.

We start from the window composed as `main()` does, with profiles and data in `tmp_path`. The test
run is the made-up fixture of phase 4 (threshold 168 bpm, pace 4:38 per km, 14 March 2026); the
profile starts with the estimated threshold of 171 bpm. No real person.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtWidgets import QLabel, QMenu
from pytestqt.qtbot import QtBot

from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.profiles.measurements import MeasurementUpdate, UpdateMeasurements
from ponos_coach.ui.data_thread import JobOutcome, JobSucceeded
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.settings_page import SettingsCategory
from ponos_coach.ui.threshold_dialogs import ThresholdInstructionsDialog
from ponos_coach.ui.threshold_preferences import ProtocolPreference
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController
from tests.ui.test_main_window import open_message_box
from tests.ui.threshold_support import (
    BIKE,
    NO_DISTANCE_RUN,
    PROFILE,
    SHORT_RUN,
    THRESHOLD_RUN,
    apply_test,
    first_load,
    open_history,
    open_review,
    plain,
    select_run,
    select_where,
    window_with,
)
from tests.ui.workspace_support import WAIT_MS, close_window, compose, make_settings

NO_TEST = "Threshold test: No test yet"
OVERDUE = "Threshold test: The last test is 108 days old: a new test is recommended"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    """Numbers and dates in English format, whatever the language of the test computer."""
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def wait_status(qtbot: QtBot, window: MainWindow, text: str) -> None:
    qtbot.waitUntil(lambda: window.retest_status_text == text, timeout=WAIT_MS)


def wait_changed_load(qtbot: QtBot, window: MainWindow, before: float) -> None:
    """Wait for an overview whose first load is not `before`."""
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and first_load(window) != pytest.approx(before)
        ),
        timeout=WAIT_MS,
    )


def wait_recalculated(qtbot: QtBot, window: MainWindow, load: float) -> None:
    """Wait for an overview whose first load is `load`."""
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and first_load(window) == pytest.approx(load)
        ),
        timeout=WAIT_MS,
    )


# --- Menu, shortcuts and tooltips -------------------------------------------------------------


def menu_commands(window: MainWindow, title: str) -> list[tuple[str, str]]:
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == title)
    assert isinstance(menu, QMenu)
    return [(a.text(), a.shortcut().toString()) for a in menu.actions()]


def test_there_is_no_threshold_menu_and_its_commands_are_in_profile_help_and_settings(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)

    titles = [a.text() for a in window.menuBar().actions()]
    assert "Threshold test" not in titles
    assert "Edit" not in titles
    profile = menu_commands(window, "Profile")
    assert ("Use as threshold test...", "Ctrl+Shift+U") in profile
    assert ("Threshold tests...", "Ctrl+Shift+R") in profile
    assert ("Test instructions...", "Ctrl+Shift+H") in menu_commands(window, "Help")
    panel = window.threshold.history_panel
    assert window.settings_window.page(SettingsCategory.TRAINING_PLAN).isAncestorOf(panel)
    assert panel.undo_button.text() == "Undo"
    assert not panel.undo_button.isEnabled()
    assert panel.undo_button.toolTip() == "There is no threshold test to take back."


def test_threshold_tests_opens_the_training_plan_page_and_the_buttons_follow_the_undo(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    qtbot.waitUntil(window.threshold_history_action.isEnabled, timeout=WAIT_MS)

    window.threshold_history_action.trigger()

    assert window.settings_window.current_category is SettingsCategory.TRAINING_PLAN
    panel = window.threshold.history_panel
    qtbot.waitUntil(panel.undo_button.isEnabled, timeout=WAIT_MS)
    assert panel.undo_button.text() == "Undo Apply threshold test"
    panel.undo_button.click()
    qtbot.waitUntil(lambda: panel.model.rowCount() == 0, timeout=WAIT_MS)
    assert panel.redo_button.isEnabled()


def test_use_as_test_says_why_it_is_disabled_without_a_selection_and_for_a_bike_ride(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN, BIKE)
    action = window.use_as_test_action

    window.activities_page.table.clearSelection()
    window.activities_page.table.setCurrentIndex(window.activities_page.proxy.index(-1, -1))
    window.controller.busy_changed.emit()
    assert not action.isEnabled()
    assert action.toolTip() == "Select an activity in the Activities list first."

    select_where(window, lambda a: a.sport.name == "CYCLING")
    qtbot.waitUntil(lambda: action.toolTip() == "Only a run can be used as a threshold test.")
    assert not action.isEnabled()

    select_run(window)
    qtbot.waitUntil(action.isEnabled)
    assert "Only a run" not in action.toolTip()  # no reason: Qt shows the name of the command


def test_without_a_profile_the_commands_say_to_create_one_first(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert not window.use_as_test_action.isEnabled()
    assert window.use_as_test_action.toolTip() == "Create or open a profile first."
    assert not window.threshold_history_action.isEnabled()
    assert window.threshold_instructions_action.isEnabled()  # the instructions need no profile
    assert not window.threshold.undo_action.isEnabled()
    assert window.threshold.undo_action.toolTip() == "There is no threshold test to take back."


# --- Instructions -----------------------------------------------------------------------------


def test_the_instructions_give_the_protocol_and_the_health_notice(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)

    window.threshold_instructions_action.trigger()

    dialog = window.findChild(ThresholdInstructionsDialog)
    assert dialog is not None
    text = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "Warm up for 10 to 15 minutes" in text
    assert "hardest effort you can keep steady" in text
    assert "30-minute test: run for 30 minutes." in text
    assert "averages of the last 20 minutes" in text
    assert "20-minute test: run for 20 minutes." in text
    assert "95% of the average of the whole test" in text
    assert "average pace made 5% slower" in text
    assert "alone and on flat ground" in text
    assert "every 6 to 8 weeks" in text
    assert "not a diagnosis" in text
    assert "talk to a doctor" in text
    dialog.close()


# --- Review of an activity as the test --------------------------------------------------------


def test_the_review_shows_day_segment_threshold_pace_current_threshold_and_the_zones(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)

    dialog = open_review(qtbot, window)

    assert dialog.is_reviewing
    assert dialog.value_text("day") == "3/14/26"
    assert plain(dialog.value_text("start")) == "8:10 AM"  # the segment starts 10 minutes in
    assert dialog.value_text("selection") == "The 30 minutes in which you covered the most distance"
    assert dialog.value_text("threshold") == "168"
    assert dialog.value_text("pace") == "4:38"
    assert dialog.value_text("current") == "171"
    assert dialog.apply_button.text() == "Apply threshold"
    assert dialog.apply_button.isEnabled()
    assert dialog.close_button.text().replace("&", "") == "Close"
    model = dialog.zone_model
    assert [model.index(row, 0).data() for row in range(model.rowCount())] == [
        "Z1",
        "Z2",
        "Z3",
        "Z4",
        "Z5",
    ]
    assert model.index(4, 1).data() == "174 or more"
    assert model.index(4, 2).data() == "4:38 or faster"
    for column in (1, 2):
        alignment = model.index(0, column).data(Qt.ItemDataRole.TextAlignmentRole)
        assert int(alignment) & int(Qt.AlignmentFlag.AlignRight)
    dialog.close_button.click()
    assert window.threshold.result_dialog is None


def test_the_review_offers_both_protocols_and_starts_with_the_30_minute_test(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)

    dialog = open_review(qtbot, window)

    assert dialog.protocol is ThresholdProtocol.THIRTY_MINUTES
    assert dialog.protocol_button(ThresholdProtocol.THIRTY_MINUTES).text() == (
        "30-minute test (last 20 minutes)"
    )
    assert dialog.protocol_button(ThresholdProtocol.TWENTY_MINUTES).text() == (
        "20-minute test (\u22125%)"
    )
    assert dialog.value_text("protocol") == "30-minute test (last 20 minutes)"
    dialog.close_button.click()


def test_choosing_the_20_minute_test_evaluates_again_and_is_remembered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)
    dialog = open_review(qtbot, window)
    thirty_bpm = dialog.value_text("threshold")

    dialog.protocol_button(ThresholdProtocol.TWENTY_MINUTES).click()

    assert dialog.is_loading  # the activity is evaluated again with the other protocol
    qtbot.waitUntil(lambda: dialog.is_reviewing, timeout=WAIT_MS)
    assert dialog.value_text("protocol") == "20-minute test (\u22125%)"
    assert dialog.value_text("selection") == "The 20 minutes in which you covered the most distance"
    assert dialog.value_text("threshold") != thirty_bpm  # 95% of the mean, not the last 20 minutes
    assert settings.value("threshold/protocol") == "twenty_minutes"
    dialog.close_button.click()

    reopened = open_review(qtbot, window)  # the last choice is the starting one

    assert reopened.protocol is ThresholdProtocol.TWENTY_MINUTES
    assert reopened.value_text("protocol") == "20-minute test (\u22125%)"
    reopened.close_button.click()


def test_a_run_too_short_for_the_30_minute_test_can_be_the_20_minute_test(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, SHORT_RUN)  # 20 minutes of running
    select_run(window)
    dialog = open_review(qtbot, window)
    assert not dialog.is_reviewing
    assert "The activity is shorter than 30 minutes." in dialog.message_page.body_text

    dialog.protocol_button(ThresholdProtocol.TWENTY_MINUTES).click()

    qtbot.waitUntil(lambda: dialog.is_reviewing, timeout=WAIT_MS)
    assert dialog.apply_button.isEnabled()
    dialog.close_button.click()


def test_the_history_names_the_protocol_the_test_was_applied_with(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    ProtocolPreference(settings).save(ThresholdProtocol.TWENTY_MINUTES)

    apply_test(qtbot, window)
    dialog = open_history(qtbot, window)

    qtbot.waitUntil(lambda: dialog.model.rowCount() == 1, timeout=WAIT_MS)
    assert dialog.proxy.index(0, 4).data() == "20-minute test, method 2"
    dialog.close()


def test_a_run_without_distance_is_evaluated_by_heart_rate_and_has_no_pace(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, NO_DISTANCE_RUN)
    select_run(window)

    dialog = open_review(qtbot, window)

    assert dialog.value_text("pace") == "Not available: the activity has no usable distance."
    assert dialog.value_text("selection").startswith("The 30 minutes with the highest heart rate")
    assert dialog.zone_model.columnCount() == 2
    dialog.close_button.click()


def test_a_short_run_is_rejected_with_the_reason_and_the_way_forward(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, SHORT_RUN)
    select_run(window)

    dialog = open_review(qtbot, window)

    assert not dialog.is_reviewing
    assert not dialog.apply_button.isEnabled()
    assert dialog.apply_button.toolTip() == "This activity cannot be applied as a threshold test."
    page = dialog.message_page
    assert page.title_text == "This activity cannot be used as a threshold test"
    assert "The activity is shorter than 30 minutes." in page.body_text
    assert "Choose an activity with at least 30 minutes of running" in page.body_text
    dialog.close_button.click()
    assert window.threshold.undo_stack.count() == 0


def test_an_activity_whose_file_is_gone_gets_the_same_message_as_the_detail(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    archive = tmp_path / "profiles" / PROFILE / PROFILE_ARCHIVE_DIR
    for original in archive.rglob("*"):
        if original.is_file():
            original.unlink()
    select_run(window)

    dialog = open_review(qtbot, window)

    page = dialog.message_page
    assert page.title_text == "The activity detail is not available"
    assert "no longer in the archive" in page.body_text
    assert not dialog.apply_button.isEnabled()
    dialog.close_button.click()


def test_closing_the_review_while_it_evaluates_drops_the_answer(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    activity = select_run(window)

    window.threshold.start_test(activity.identity)
    dialog = window.threshold.result_dialog
    assert dialog is not None
    assert dialog.is_loading  # an indeterminate wait, not a frozen window
    assert not dialog.apply_button.isEnabled()
    dialog.close()
    qtbot.wait(300)  # the answer arrives, finds no dialog and is dropped

    assert window.threshold.result_dialog is None
    assert window.threshold.undo_stack.count() == 0


# --- Apply, undo, redo ------------------------------------------------------------------------


def test_applying_the_test_updates_the_threshold_the_load_and_the_retest_status(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    wait_status(qtbot, window, NO_TEST)
    before = first_load(window)

    apply_test(qtbot, window)

    stack = window.threshold.undo_stack
    assert stack.count() == 1
    assert stack.undoText() == "Apply threshold test"
    assert window.threshold.undo_action.text() == "Undo Apply threshold test"
    message = window.statusBar().currentMessage()
    assert message.startswith("Threshold test applied: 168 bpm.")
    assert "Heart rate valid from 3/14/26 to 6/29/26." in message
    assert message.endswith("The load is being recalculated.")
    wait_changed_load(qtbot, window, before)
    wait_status(qtbot, window, OVERDUE)


def test_undo_takes_the_test_back_and_redo_applies_it_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    before = first_load(window)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)
    wait_changed_load(qtbot, window, before)
    applied_load = first_load(window)

    window.threshold.undo_action.trigger()

    wait_status(qtbot, window, NO_TEST)
    wait_recalculated(qtbot, window, before)
    assert window.threshold.redo_action.isEnabled()

    window.threshold.redo_action.trigger()

    wait_status(qtbot, window, OVERDUE)
    wait_recalculated(qtbot, window, applied_load)


def test_an_undo_refused_because_the_threshold_was_edited_says_so_and_clears_the_stack(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)
    edited: list[JobOutcome] = []

    def edit_threshold(access: ProfileAccess) -> None:
        session = access.session
        test_day = session.threshold_tests.list_tests()[0].test_day
        UpdateMeasurements(
            session.activities,
            session.physiology,
            session.weights,
            access.today,
            session.fixed_data,
            session.threshold_paces,
        )(MeasurementUpdate(test_day, threshold_bpm=170))

    assert window.controller.run_on_profile(edit_threshold, edited.append)
    qtbot.waitUntil(lambda: bool(edited), timeout=WAIT_MS)
    assert isinstance(edited[0], JobSucceeded)

    window.threshold.undo_action.trigger()

    box = open_message_box(qtbot, window, "errorMessage")
    assert box.text() == "The threshold test cannot be taken back"
    assert "The threshold was changed after the test" in box.informativeText()
    assert "Profile > Edit profile" in box.informativeText()
    assert window.threshold.undo_stack.count() == 0  # the stack matches what is stored again
    assert not window.threshold.undo_action.isEnabled()
    wait_status(qtbot, window, OVERDUE)  # the test is still saved
    box.close()


def test_applying_while_the_profile_is_busy_keeps_the_review_open_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)
    dialog = open_review(qtbot, window)
    monkeypatch.setattr(WorkspaceController, "can_edit_profile", property(lambda _self: False))

    dialog.apply_button.click()

    assert dialog.failure_text.startswith("The profile is busy.")
    assert dialog.apply_button.isEnabled()  # it can be tried again
    assert window.threshold.undo_stack.count() == 0
    dialog.close_button.click()


def test_the_same_activity_cannot_be_applied_twice(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)
    wait_status(qtbot, window, OVERDUE)
    select_run(window)
    dialog = open_review(qtbot, window)

    dialog.apply_button.click()

    qtbot.waitUntil(lambda: dialog.failure_text != "", timeout=WAIT_MS)
    assert dialog.failure_text.startswith("This activity is already a threshold test.")
    assert window.threshold.undo_stack.count() == 1
    dialog.close_button.click()


# --- History ----------------------------------------------------------------------------------


def test_the_history_is_empty_at_first_and_says_how_to_add_a_test(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)

    dialog = open_history(qtbot, window)

    qtbot.waitUntil(lambda: dialog.retest_label_text == "No test yet", timeout=WAIT_MS)
    assert dialog.empty_page.title_text == "No threshold tests yet"
    assert "Use as threshold test" in dialog.empty_page.body_text


def test_the_history_lists_the_test_with_the_activity_pace_and_method_and_the_retest_state(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)

    dialog = open_history(qtbot, window)

    qtbot.waitUntil(lambda: dialog.model.rowCount() == 1, timeout=WAIT_MS)
    headers = [dialog.model.headerData(c, Qt.Orientation.Horizontal) for c in range(5)]
    assert headers == [
        "Date",
        "Activity",
        "Threshold heart rate (bpm)",
        "Threshold pace (min:s per km)",
        "Method",
    ]
    cells = [dialog.proxy.index(0, c).data() for c in range(5)]
    assert cells[0] == "3/14/26"
    assert plain(cells[1]) == "Running, 3/14/26 8:00 AM"  # 07:00 UTC is 08:00 in Rome
    assert cells[2:] == ["168", "4:38", "30-minute test, method 2"]
    qtbot.waitUntil(
        lambda: dialog.retest_label_text.startswith("The last test is"), timeout=WAIT_MS
    )
    assert dialog.retest_label_text == "The last test is 108 days old: a new test is recommended"


def test_the_open_history_follows_an_undo(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)
    dialog = open_history(qtbot, window)
    qtbot.waitUntil(lambda: dialog.model.rowCount() == 1, timeout=WAIT_MS)

    window.threshold.undo_stack.undo()

    qtbot.waitUntil(lambda: dialog.model.rowCount() == 0, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: dialog.retest_label_text == "No test yet", timeout=WAIT_MS)


def test_the_retest_status_in_the_status_bar_is_hidden_without_text_and_comes_back(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    wait_status(qtbot, window, NO_TEST)

    window.threshold.retest_text_changed.emit("")

    assert window.retest_status_text == ""
    window.threshold.data_changed()
    wait_status(qtbot, window, NO_TEST)


# --- From the activity detail window ----------------------------------------------------------


def test_the_detail_window_offers_the_test_for_a_run_and_opens_the_same_review(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)
    window.detail_action.trigger()
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is not None, timeout=WAIT_MS)
    detail = window.profile_dialogs.detail_dialog
    assert detail is not None
    qtbot.waitUntil(detail.use_as_test_button.isEnabled, timeout=WAIT_MS)

    detail.use_as_test_button.click()

    qtbot.waitUntil(lambda: window.threshold.result_dialog is not None, timeout=WAIT_MS)
    dialog = window.threshold.result_dialog
    assert dialog is not None
    qtbot.waitUntil(lambda: dialog.is_reviewing, timeout=WAIT_MS)
    assert dialog.value_text("threshold") == "168"
    dialog.close_button.click()
    detail.close()


def test_the_detail_window_disables_the_test_for_a_bike_ride_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, BIKE)
    select_where(window, lambda a: a.sport.name == "CYCLING")
    window.detail_action.trigger()
    qtbot.waitUntil(lambda: window.profile_dialogs.detail_dialog is not None, timeout=WAIT_MS)
    detail = window.profile_dialogs.detail_dialog
    assert detail is not None
    qtbot.waitUntil(detail.shows_chart, timeout=WAIT_MS)

    assert not detail.use_as_test_button.isEnabled()
    assert detail.use_as_test_button.toolTip() == "Only a run can be used as a threshold test."
    detail.close()


# --- Closing and logs -------------------------------------------------------------------------


def test_closing_the_window_with_the_review_open_finishes_the_closing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    select_run(window)
    open_review(qtbot, window)

    close_window(qtbot, window)

    assert window.threshold.result_dialog is None


def test_the_threshold_and_pace_never_reach_the_logs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)
    window.threshold.undo_action.trigger()
    wait_status(qtbot, window, NO_TEST)

    logged = caplog.text.lower()

    assert "168" not in logged
    assert "4:38" not in logged
    assert "threshold" not in logged


def test_an_undo_that_cannot_start_clears_the_stack_and_says_the_profile_is_busy(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = window_with(qtbot, tmp_path, settings, THRESHOLD_RUN)
    apply_test(qtbot, window)
    wait_status(qtbot, window, OVERDUE)
    monkeypatch.setattr(window.threshold.loader, "remove", lambda _cell: False)

    window.threshold.undo_action.trigger()

    box = open_message_box(qtbot, window, "errorMessage")
    assert box.text() == "The profile is busy"
    qtbot.waitUntil(lambda: window.threshold.undo_stack.count() == 0)
    box.close()
