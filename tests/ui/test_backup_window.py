# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile backup and restore from the main window (phase 8): round trip, errors, list entry."""

from __future__ import annotations

import threading
import zipfile
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.profiles.backup import BackupProfile
from ponos_coach.profiles.domain.backup import BackupSummary
from ponos_coach.profiles.ports import (
    BackupFileExistsError,
    BackupInvalidError,
    BackupProblem,
    BackupTooNewError,
    BackupWriteError,
    FolderNotEmptyError,
)
from ponos_coach.ui.backup_runner import BackupUseCases
from ponos_coach.ui.backup_text import (
    backup_error_message,
    restore_error_message,
    suggested_backup_name,
)
from ponos_coach.ui.main_window import MainWindow
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    TODAY,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    make_import_folder,
    make_settings,
    wait_ready,
)

NAME = "Atleta di prova"
ACTIVITIES = FILES_IN_FIXTURE_FOLDER


class FakePickers:
    """Answers the three dialogs with what the test sets and remembers where they started."""

    def __init__(self) -> None:
        self.save = ""
        self.open = ""
        self.folder = ""
        self.starts: list[str] = []

    def save_file(self, _parent: QWidget, _title: str, start: str, _filter: str) -> str:
        self.starts.append(start)
        return self.save

    def open_file(self, _parent: QWidget, _title: str, start: str, _filter: str) -> str:
        self.starts.append(start)
        return self.open

    def choose_folder(self, _parent: QWidget, _title: str, start: str) -> str:
        self.starts.append(start)
        return self.folder


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def window_with_activities(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> tuple[MainWindow, FakePickers]:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, NAME)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))
    wait_ready(qtbot, window, ACTIVITIES)
    pickers = FakePickers()
    window.backup.pickers.save_file = pickers.save_file
    window.backup.pickers.open_file = pickers.open_file
    window.backup.pickers.folder = pickers.choose_folder
    return window, pickers


def wait_for_box(qtbot: QtBot, window: MainWindow, name: str) -> QMessageBox:
    def find() -> QMessageBox | None:
        return next(
            (
                box
                for box in window.open_boxes
                if isinstance(box, QMessageBox) and box.objectName() == name and box.isVisible()
            ),
            None,
        )

    qtbot.waitUntil(lambda: find() is not None, timeout=WAIT_MS)
    box = find()
    assert box is not None
    return box


def click(box: QMessageBox, text: str) -> None:
    next(button for button in box.buttons() if button.text() == text).click()


def back_up(qtbot: QtBot, window: MainWindow, pickers: FakePickers, target: Path) -> QMessageBox:
    pickers.save = str(target)
    window.backup.backup_action.trigger()
    return wait_for_box(qtbot, window, "backupDone")


def restore(
    qtbot: QtBot, window: MainWindow, pickers: FakePickers, source: Path, folder: Path, box: str
) -> QMessageBox:
    pickers.open = str(source)
    pickers.folder = str(folder)
    window.backup.restore_action.trigger()
    return wait_for_box(qtbot, window, box)


# --- Backup -------------------------------------------------------------------------------------


def test_backup_proposes_the_name_and_reports_size_and_activities(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    target = tmp_path / "backups" / "mine.ponosbackup"
    target.parent.mkdir()

    box = back_up(qtbot, window, pickers, target)

    assert pickers.starts[-1] == f"{NAME} {TODAY.isoformat()}.ponosbackup"
    assert zipfile.is_zipfile(target)
    assert "mine.ponosbackup" in box.text()
    assert f"Activity files: {ACTIVITIES}" in box.informativeText()
    assert "Size:" in box.informativeText()
    # The folder is remembered, separately from the folder of "Open profile from folder".
    pickers.save = ""
    window.backup.backup_action.trigger()
    assert pickers.starts[-1] == str(target.parent / Path(pickers.starts[0]).name)
    close_window(qtbot, window)


def test_backup_adds_the_suffix_when_the_user_leaves_it_out(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    target = tmp_path / "bare"
    back_up(qtbot, window, pickers, target)
    assert (tmp_path / "bare.ponosbackup").is_file()
    assert not target.exists()
    close_window(qtbot, window)


def test_backup_never_replaces_an_existing_file(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    existing = tmp_path / "taken.ponosbackup"
    existing.write_bytes(b"precious")

    pickers.save = str(existing)
    window.backup.backup_action.trigger()
    box = wait_for_box(qtbot, window, "backupError")

    assert "already exists" in box.informativeText()
    assert existing.read_bytes() == b"precious"
    close_window(qtbot, window)


def test_backup_inside_the_profile_folder_is_refused_with_a_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    profile = window.controller.profile
    assert profile is not None

    pickers.save = str(profile.folder / "inside.ponosbackup")
    window.backup.backup_action.trigger()
    box = wait_for_box(qtbot, window, "backupError")

    assert "inside the folder of the profile" in box.informativeText()
    assert not (profile.folder / "inside.ponosbackup").exists()
    close_window(qtbot, window)


def test_cancelling_the_dialog_does_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    pickers.save = ""
    window.backup.backup_action.trigger()
    assert not window.backup.runner.is_busy
    assert not any(isinstance(b, QMessageBox) and b.isVisible() for b in window.open_boxes)
    close_window(qtbot, window)


def test_backup_is_off_without_a_profile_and_says_why(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    assert not window.backup.backup_action.isEnabled()
    assert window.backup.backup_action.toolTip() == "Create or open a profile first."
    assert window.backup.restore_action.isEnabled()
    assert window.backup.backup_action.shortcut().toString() == "Ctrl+Shift+B"
    close_window(qtbot, window)


class _GatedBackup:
    """The real backup, started only when the gate opens."""

    def __init__(self, inner: BackupProfile, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate

    def __call__(self, folder: Path, destination: Path) -> BackupSummary:
        assert self._gate.wait(timeout=30)
        return self._inner(folder, destination)


def test_a_long_backup_shows_progress_and_the_window_stays_usable(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, gate: threading.Event
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    runner = window.backup.runner
    real = runner.use_cases
    gated = _GatedBackup(real.backup, gate)
    runner.use_cases = BackupUseCases(gated, real.restore)  # type: ignore[arg-type]
    window.backup.progress_delay_ms = 10
    assert not window.backup.progress_visible

    pickers.save = str(tmp_path / "slow.ponosbackup")
    window.backup.backup_action.trigger()

    qtbot.waitUntil(lambda: window.backup.progress_visible, timeout=WAIT_MS)
    assert runner.is_busy
    assert not window.backup.backup_action.isEnabled()
    assert "running" in window.backup.backup_action.toolTip()
    assert window.isEnabled()
    gate.set()
    wait_for_box(qtbot, window, "backupDone")
    assert not window.backup.progress_visible
    assert window.backup.backup_action.isEnabled()
    close_window(qtbot, window)


# --- Restore ------------------------------------------------------------------------------------


def test_round_trip_replaces_the_list_entry_and_opens_the_restored_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    backup_file = tmp_path / "round.ponosbackup"
    back_up(qtbot, window, pickers, backup_file)
    restored = tmp_path / "restored"

    box = restore(qtbot, window, pickers, backup_file, restored, "restoreDone")

    assert (restored / "ponos.sqlite3").is_file()
    assert f"Activity files: {ACTIVITIES}" in box.informativeText()
    # The same id is already in the list: the box says so and the buttons say the verb.
    assert "same id" in box.informativeText()
    assert {b.text() for b in box.buttons()} == {"Replace entry", "Keep the list as it is"}
    click(box, "Replace entry")
    qtbot.waitUntil(
        lambda: (
            window.controller.profile is not None and window.controller.profile.folder == restored
        ),
        timeout=WAIT_MS,
    )
    wait_ready(qtbot, window, ACTIVITIES)
    assert [entry.folder for entry in window.controller.profiles] == [restored]
    close_window(qtbot, window)


def test_keeping_the_list_leaves_the_profile_open_and_the_entry_alone(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    backup_file = tmp_path / "keep.ponosbackup"
    back_up(qtbot, window, pickers, backup_file)
    original = window.controller.profile
    assert original is not None

    box = restore(qtbot, window, pickers, backup_file, tmp_path / "restored", "restoreDone")
    click(box, "Keep the list as it is")

    assert window.controller.profile is not None
    assert window.controller.profile.folder == original.folder
    assert [entry.folder for entry in window.controller.profiles] == [original.folder]
    close_window(qtbot, window)


def test_restore_of_an_unlisted_profile_offers_to_open_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    backup_file = tmp_path / "unlisted.ponosbackup"
    back_up(qtbot, window, pickers, backup_file)
    profile = window.controller.profile
    assert profile is not None
    assert window.controller.remove_profile(profile.profile_id)
    qtbot.waitUntil(lambda: not window.controller.profiles, timeout=WAIT_MS)
    restored = tmp_path / "again"

    box = restore(qtbot, window, pickers, backup_file, restored, "restoreDone")

    assert {b.text() for b in box.buttons()} == {"Open profile", "Not now"}
    assert "same id" not in box.informativeText()
    click(box, "Open profile")
    qtbot.waitUntil(
        lambda: (
            window.controller.profile is not None and window.controller.profile.folder == restored
        ),
        timeout=WAIT_MS,
    )
    close_window(qtbot, window)


def test_restore_into_a_folder_that_is_not_empty_is_refused_and_nothing_changes(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    backup_file = tmp_path / "full.ponosbackup"
    back_up(qtbot, window, pickers, backup_file)
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "mine.txt").write_text("keep me", encoding="utf-8")

    box = restore(qtbot, window, pickers, backup_file, taken, "restoreError")

    assert "not empty" in box.informativeText()
    assert [p.name for p in taken.iterdir()] == ["mine.txt"]
    close_window(qtbot, window)


def test_restore_of_a_file_that_is_not_a_backup_says_so(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    junk = tmp_path / "junk.ponosbackup"
    junk.write_bytes(b"this is not a backup")
    target = tmp_path / "never"

    box = restore(qtbot, window, pickers, junk, target, "restoreError")

    assert "not a Ponos Coach backup" in box.informativeText()
    assert not target.exists() or not any(target.iterdir())
    close_window(qtbot, window)


def test_restore_dialogs_remember_their_own_folders(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, pickers = window_with_activities(qtbot, tmp_path, settings)
    junk = tmp_path / "files" / "junk.ponosbackup"
    junk.parent.mkdir()
    junk.write_bytes(b"x")
    restore(qtbot, window, pickers, junk, tmp_path / "out" / "new", "restoreError")

    pickers.open = ""
    pickers.starts.clear()
    window.backup.restore_action.trigger()

    assert pickers.starts == [str(junk.parent)]
    close_window(qtbot, window)


# --- Messages: one per reason -------------------------------------------------------------------


def test_every_reason_of_failure_has_its_own_plain_message() -> None:
    backup_reasons: list[Exception] = [BackupFileExistsError(), BackupWriteError(), ValueError()]
    restore_reasons: list[Exception] = [
        BackupTooNewError(),
        FolderNotEmptyError("x"),
        BackupWriteError(),
        *[BackupInvalidError(problem) for problem in BackupProblem],
    ]
    backup_messages = [backup_error_message(error) for error in backup_reasons]
    restore_messages = [restore_error_message(error) for error in restore_reasons]
    assert len({m.what for m in backup_messages}) == len(backup_messages)
    assert len({m.what for m in restore_messages}) == len(restore_messages)
    for message in (*backup_messages, *restore_messages):
        assert message.remedy
        assert "Error" not in message.what  # no exception names for the user


def test_suggested_name_is_profile_and_date_and_a_valid_file_name() -> None:
    assert suggested_backup_name("Ada", date(2026, 1, 2)) == "Ada 2026-01-02.ponosbackup"
    assert suggested_backup_name('a/b:c"', date(2026, 1, 2)) == "a-b-c- 2026-01-02.ponosbackup"
