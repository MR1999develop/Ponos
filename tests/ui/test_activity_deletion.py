# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Deleting activities from the Activities list: the row menu, Del, the menu, the undo and the
window "Deleted activities".

From the Composition Root, with the made-up profile "Atleta di prova" and the fake Garmin of
`tests/fakes/` (no network). The acceptance test is the first: an activity deleted from the list
does not come back with the next synchronization.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.import_report_dialog import ImportReportDialog
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.sync_model import AccountStatus
from tests.fakes.garmin import FakeActivitySource, make_remote_history
from tests.fixtures.generators.import_files import fit_run
from tests.ui.garmin_support import (
    PROFILE_NAME,
    activity_count,
    link_account,
    sync_and_wait,
    wait_account,
    wait_activities,
)
from tests.ui.workspace_support import (
    WAIT_MS,
    compose,
    create_profile,
    make_fakes,
    make_settings,
)

RUNS = 4


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def write_runs(folder: Path, numbers: range) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for number in numbers:
        path = folder / f"corsa-{number}.fit"
        path.write_bytes(fit_run(number))
        paths.append(path)
    return paths


def import_runs(qtbot: QtBot, window: MainWindow, paths: list[Path]) -> ImportReport:
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.import_files(paths)
    report = blocker.args[0] if blocker.args else None
    assert isinstance(report, ImportReport)
    return report


def window_with_runs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, *, garmin_runs: int = 0
) -> MainWindow:
    """A profile with the runs 0 to 3 imported from files; a fake Garmin that has `garmin_runs`."""
    fakes = make_fakes(
        mfa_required=False, source=FakeActivitySource(make_remote_history(garmin_runs))
    )
    window = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)
    wait_account(qtbot, window, AccountStatus.NOT_LINKED)
    import_runs(qtbot, window, write_runs(tmp_path / "files", range(RUNS)))
    wait_activities(qtbot, window, RUNS)
    return window


def identities(window: MainWindow) -> list[ActivityIdentity]:
    """The identities the overview shows, most recent first (the order of the list)."""
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    return [
        a.identity for a in sorted(state.overview.activities, key=lambda a: a.start, reverse=True)
    ]


def select(window: MainWindow, *rows: int) -> None:
    """Select these rows of the list (0 is the most recent run)."""
    table = window.activities_page.table
    table.clearSelection()
    for row in rows:
        table.selectionModel().select(
            table.model().index(row, 0),
            table.selectionModel().SelectionFlag.Select | table.selectionModel().SelectionFlag.Rows,
        )
    table.selectionModel().setCurrentIndex(
        table.model().index(rows[0], 0), table.selectionModel().SelectionFlag.NoUpdate
    )


def press(
    qtbot: QtBot,
    window: MainWindow,
    key: Qt.Key,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> None:
    """A key pressed while the list has the focus (the window is made active first: a shortcut
    only fires in the active window)."""
    table = window.activities_page.table
    window.activateWindow()
    window.raise_()
    table.setFocus()
    qtbot.waitUntil(lambda: table.hasFocus() and window.isActiveWindow(), timeout=WAIT_MS)
    QTest.keyClick(table, key, modifiers)


def status_text(window: MainWindow) -> str:
    return window.statusBar().currentMessage()


def right_click_row(window: MainWindow, row: int) -> QMenu:
    table = window.activities_page.table
    position = table.visualRect(table.model().index(row, 0)).center()
    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, position, table.viewport().mapToGlobal(position)
    )
    QApplication.sendEvent(table.viewport(), event)
    menu = window.activities_page.row_menu
    assert menu is not None
    return menu


def profile_action(window: MainWindow, text: str) -> QAction:
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Profile")
    assert isinstance(menu, QMenu)
    return next(a for a in menu.actions() if a.text().startswith(text))


# --- Acceptance: a deleted activity does not come back with the synchronization -------------


def test_a_deleted_activity_does_not_come_back_with_the_next_synchronization(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings, garmin_runs=RUNS)
    select(window, 1)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)

    link_account(qtbot, window, mfa=False)  # Garmin lists the four runs again
    sync_and_wait(qtbot, window, verify=True)

    wait_activities(qtbot, window, RUNS - 1)  # the overview the round may have asked for
    assert window.controller.sync.account_status is AccountStatus.LINKED
    assert len(window.activity_deletion.page.model.rows) == 1  # still deleted, still listed


# --- Delete: Del, the menu bar, the row menu ---------------------------------------------------


def test_del_deletes_the_selected_activity_at_once_and_the_status_bar_says_how_to_undo(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0)

    press(qtbot, window, Qt.Key.Key_Delete)

    wait_activities(qtbot, window, RUNS - 1)
    message = status_text(window)
    assert message.startswith("Deleted Running, ")
    assert (
        f"Press {QKeySequence(QKeySequence.StandardKey.Undo).toString()} to bring it back."
        in message
    )
    assert window.activity_deletion.undo_action.text() == "Undo Delete activity"


def test_the_shortcuts_act_only_while_the_list_has_the_focus(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    deletion = window.activity_deletion

    assert deletion.delete_action.shortcuts() == QKeySequence.keyBindings(
        QKeySequence.StandardKey.Delete
    )
    assert deletion.undo_action.shortcuts() == QKeySequence.keyBindings(
        QKeySequence.StandardKey.Undo
    )
    assert deletion.redo_action.shortcuts() == QKeySequence.keyBindings(
        QKeySequence.StandardKey.Redo
    )
    for action in (deletion.delete_action, deletion.undo_action, deletion.redo_action):
        assert action.shortcutContext() == Qt.ShortcutContext.WidgetWithChildrenShortcut
        assert action in window.activities_page.actions()


def test_the_menu_bar_has_the_commands_with_the_reason_when_one_cannot_be_given(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    window.activities_page.table.clearSelection()
    delete = profile_action(window, "Delete activity")

    assert not delete.isEnabled()
    assert delete.toolTip() == "Select an activity in the Activities list first."
    assert delete is window.activity_deletion.delete_action
    assert profile_action(window, "Deleted activities...").isEnabled()
    assert profile_action(window, "Undo") is window.activity_deletion.undo_action
    select(window, 2)
    assert delete.isEnabled()
    assert delete.toolTip() == delete.text()  # no reason: Qt shows the name


def test_the_row_menu_acts_on_the_row_under_the_pointer_and_deletes_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0)
    ids = identities(window)

    menu = right_click_row(window, 2)  # a row that was not selected

    assert window.activities_page.selected_identities() == (ids[2],)
    texts = [a.text() for a in menu.actions() if not a.isSeparator()]
    assert texts == ["Activity detail...", "Delete activity"]
    delete = next(a for a in menu.actions() if a.text() == "Delete activity")
    assert delete.shortcut() == QKeySequence(QKeySequence.StandardKey.Delete)
    delete.trigger()
    menu.close()
    wait_activities(qtbot, window, RUNS - 1)
    assert ids[2] not in identities(window)


def test_the_row_menu_of_several_rows_deletes_them_all_in_one_step(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0, 1)
    ids = identities(window)

    menu = right_click_row(window, 0)

    delete = next(a for a in menu.actions() if a.text().startswith("Delete"))
    assert delete.text() == "Delete 2 activities"
    delete.trigger()
    menu.close()
    wait_activities(qtbot, window, RUNS - 2)
    assert window.activity_deletion.undo_stack.count() == 1
    assert "2 activities deleted." in status_text(window)
    assert not {ids[0], ids[1]} & set(identities(window))


def test_a_right_click_below_the_rows_opens_no_menu(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    table = window.activities_page.table
    position = QPoint(5, table.viewport().height() + 10_000)  # far below the last row

    event = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, position, table.viewport().mapToGlobal(position)
    )
    QApplication.sendEvent(table.viewport(), event)

    assert window.activities_page.row_menu is None


def test_nothing_is_asked_and_nothing_happens_without_a_selection(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    window.activities_page.table.clearSelection()

    press(qtbot, window, Qt.Key.Key_Delete)

    assert window.activity_deletion.undo_stack.count() == 0
    assert activity_count(window) == RUNS
    assert not [
        b for b in window.open_boxes if isinstance(b, QMessageBox) and b.isVisible()
    ]  # no "Are you sure?"


# --- Undo and redo --------------------------------------------------------------------------


def test_the_undo_brings_the_activity_back_exactly_and_the_redo_deletes_it_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    before = identities(window)
    select(window, 1)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)

    press(qtbot, window, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    wait_activities(qtbot, window, RUNS)
    assert identities(window) == before
    assert window.activity_deletion.page.model.rows == ()
    assert window.activity_deletion.redo_action.text() == "Redo Delete activity"
    press(
        qtbot,
        window,
        Qt.Key.Key_Z,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    wait_activities(qtbot, window, RUNS - 1)
    assert before[1] not in identities(window)


def test_restoring_goes_through_the_same_stack_and_its_undo_deletes_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 3)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)
    deletion = window.activity_deletion
    deletion.show_window()
    qtbot.waitUntil(lambda: len(deletion.page.model.rows) == 1, timeout=WAIT_MS)
    deletion.page.table.selectRow(0)

    deletion.page.restore_button.click()

    wait_activities(qtbot, window, RUNS)
    assert status_text(window) == "Activity restored."
    assert deletion.undo_action.text() == "Undo Restore activity"
    deletion.undo_action.trigger()
    wait_activities(qtbot, window, RUNS - 1)


# --- The window "Deleted activities" -----------------------------------------------------------


def test_the_window_says_so_when_nothing_is_deleted(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    deletion = window.activity_deletion

    profile_action(window, "Deleted activities...").trigger()

    assert deletion.window.isVisible()
    assert deletion.window.windowTitle() == "Deleted activities"
    assert deletion.page.is_empty_state
    assert deletion.page.empty_text.startswith("No deleted activities.")
    assert not deletion.page.restore_button.isEnabled()


def test_the_window_lists_date_sport_duration_and_distance_and_restores_the_selection(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0, 2)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 2)
    deletion = window.activity_deletion

    deletion.show_window()
    qtbot.waitUntil(lambda: len(deletion.page.model.rows) == 2, timeout=WAIT_MS)

    page = deletion.page
    assert not page.is_empty_state
    headers = [
        page.model.headerData(c, Qt.Orientation.Horizontal) for c in range(page.model.columnCount())
    ]
    assert headers == ["Date and time", "Sport", "Total time (h:mm:ss)", "Distance (km)"]
    for _start, sport, duration, _distance in page.model.rows:
        assert (sport, duration) == ("Running", "0:30:00")
    assert not page.restore_button.isEnabled()
    assert page.restore_button.toolTip() == "Select a deleted activity in the list first."
    page.table.selectRow(0)
    assert page.restore_button.isEnabled()
    page.restore_button.click()
    wait_activities(qtbot, window, RUNS - 1)
    qtbot.waitUntil(lambda: len(page.model.rows) == 1, timeout=WAIT_MS)


def test_the_window_is_not_modal_and_the_list_stays_usable(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)

    window.activity_deletion.show_window()

    assert not window.activity_deletion.window.isModal()


def test_a_restored_activity_whose_file_is_gone_stays_deleted_and_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)
    for archived in (tmp_path / "profiles" / PROFILE_NAME / "archivio").rglob("*.fit"):
        archived.unlink()
    deletion = window.activity_deletion
    deletion.show_window()
    qtbot.waitUntil(lambda: len(deletion.page.model.rows) == 1, timeout=WAIT_MS)
    shown: list[object] = []
    deletion._show_error = shown.append

    deletion.page.table.selectRow(0)
    deletion.page.restore_button.click()

    qtbot.waitUntil(lambda: bool(shown), timeout=WAIT_MS)
    # The outcome has already asked for a new overview (Loading): wait for it, not the old one.
    wait_activities(qtbot, window, RUNS - 1)
    assert len(deletion.page.model.rows) == 1


# --- The imports ------------------------------------------------------------------------------


def test_importing_the_file_by_hand_brings_the_activity_back_and_the_summary_says_so(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)

    report = import_runs(qtbot, window, [tmp_path / "files" / "corsa-3.fit"])  # the most recent

    assert (report.brought_back, report.imported, report.left_out) == (1, 0, 0)
    wait_activities(qtbot, window, RUNS)
    dialog = [b for b in window.open_boxes if isinstance(b, ImportReportDialog)][-1]
    assert "Deleted activities brought back by their file: 1" in dialog.summary_text
    window.activity_deletion.show_window()
    qtbot.waitUntil(lambda: window.activity_deletion.page.is_empty_state, timeout=WAIT_MS)


def test_importing_a_folder_leaves_a_deleted_activity_out_and_the_summary_says_so(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_runs(qtbot, tmp_path, settings)
    select(window, 0)
    press(qtbot, window, Qt.Key.Key_Delete)
    wait_activities(qtbot, window, RUNS - 1)

    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.import_folder(tmp_path / "files")
    report = blocker.args[0]

    assert isinstance(report, ImportReport)
    assert (report.left_out, report.already_present, report.imported) == (1, RUNS - 1, 0)
    wait_activities(qtbot, window, RUNS - 1)
    dialog = [b for b in window.open_boxes if isinstance(b, ImportReportDialog)][-1]
    assert "Left out because you deleted them: 1." in dialog.summary_text
    assert "choose its file with Import files" in dialog.summary_text
