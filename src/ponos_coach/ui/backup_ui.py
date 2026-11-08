# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Backup and restore of the profile in the main window: menu commands, progress and answers.

Humble view: names, texts and error messages are in `backup_text`; the work runs on a thread of
`BackupRunner`. The window stays usable; after one second a progress indicator appears in the
status bar. A backup never replaces a file and a restore only goes to a new or empty folder (the
use cases enforce both); here the dialogs remember where they were and the answers say what
happened in plain words.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QWidget,
)

from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX
from ponos_coach.ui.backup_runner import (
    BackupFailed,
    BackupKind,
    BackupRunner,
    BackupSucceeded,
)
from ponos_coach.ui.backup_text import (
    backup_done_text,
    backup_error_message,
    restore_done_text,
    restore_error_message,
    suggested_backup_name,
)
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.tokens import SPACE_2, SPACE_4, STATUS_PROGRESS_WIDTH
from ponos_coach.ui.workspace import WorkspaceController

_KEY_BACKUP_FOLDER = "backup/lastBackupFolder"
_KEY_RESTORE_FILE_FOLDER = "backup/lastRestoreFileFolder"
_KEY_RESTORE_FOLDER = "backup/lastRestoreFolder"
_PROGRESS_DELAY_MS = 1000  # the indicator appears only when the work takes longer than this


@dataclass(slots=True)
class BackupPickers:
    """How the user chooses files and folders; each returns "" if cancelled (fake in tests)."""

    save_file: Callable[[QWidget, str, str, str], str]  # parent, title, start, name filter
    open_file: Callable[[QWidget, str, str, str], str]
    folder: Callable[[QWidget, str, str], str]


def native_pickers() -> BackupPickers:
    """The system dialogs."""
    return BackupPickers(
        save_file=lambda parent, title, start, name_filter: QFileDialog.getSaveFileName(
            parent, title, start, name_filter
        )[0],
        open_file=lambda parent, title, start, name_filter: QFileDialog.getOpenFileName(
            parent, title, start, name_filter
        )[0],
        folder=QFileDialog.getExistingDirectory,
    )


class BackupUi(QObject):
    """ "Back up profile..." and "Restore profile from backup..." in the File menu."""

    def __init__(  # noqa: PLR0913 - each argument is a piece of the window it works with
        self,
        window: QMainWindow,
        controller: WorkspaceController,
        runner: BackupRunner,
        settings: QSettings,
        *,
        today: Callable[[], date],
        remember_box: Callable[[QMessageBox], None],
        show_error: Callable[[ErrorMessage, str], None],
        open_profile_folder: Callable[[Path], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._controller = controller
        self._runner = runner
        self._settings = settings
        self._today = today
        self._remember_box = remember_box
        self._show_error = show_error
        self._open_profile_folder = open_profile_folder
        self.pickers = native_pickers()
        self.progress_delay_ms = _PROGRESS_DELAY_MS
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.timeout.connect(self._show_progress)
        self._build_actions()
        self._build_status_widgets()
        runner.started.connect(self._on_started)
        runner.finished.connect(self._on_finished)
        self.refresh()

    # --- Construction -----

    def _action(self, text: str, shortcut: str, slot: Callable[[], object]) -> QAction:
        action = QAction(text, self._window)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self._backup_action = self._action(
            self.tr("Back up profile..."), "Ctrl+Shift+B", self.back_up
        )
        self._restore_action = self._action(
            self.tr("Restore profile from backup..."), "Ctrl+Shift+O", self.restore
        )

    def _build_status_widgets(self) -> None:
        bar = self._window.statusBar()
        self._progress_label = QLabel(self._window)
        self._progress_label.setTextFormat(Qt.TextFormat.PlainText)
        self._progress_label.setContentsMargins(SPACE_2, 0, SPACE_4, 0)
        self._progress_bar = QProgressBar(self._window)
        self._progress_bar.setRange(0, 0)  # the use cases report no steps: no end in sight
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setFixedWidth(STATUS_PROGRESS_WIDTH)
        for widget in (self._progress_label, self._progress_bar):
            bar.addPermanentWidget(widget)
            widget.setVisible(False)

    def add_to_menu(self, menu: QMenu) -> None:
        """Add the two commands to the File menu."""
        menu.addAction(self._backup_action)
        menu.addAction(self._restore_action)

    # --- Accessors for the window and the tests -----

    @property
    def runner(self) -> BackupRunner:
        return self._runner

    @property
    def backup_action(self) -> QAction:
        return self._backup_action

    @property
    def restore_action(self) -> QAction:
        return self._restore_action

    @property
    def progress_visible(self) -> bool:
        """True while the indicator of a long job is shown."""
        return self._progress_bar.isVisibleTo(self._window)

    # --- State of the commands -----

    def refresh(self) -> None:
        """Enable the commands, each with the reason when it cannot be given."""
        busy = self._runner.is_busy
        busy_reason = self.tr("A backup or a restore is running; wait for it to finish.")
        if busy:
            backup_reason = busy_reason
        elif self._controller.profile is None:
            backup_reason = self.tr("Create or open a profile first.")
        else:
            backup_reason = ""
        for action, reason in (
            (self._backup_action, backup_reason),
            (self._restore_action, busy_reason if busy else ""),
        ):
            action.setEnabled(not reason)
            action.setToolTip(reason)

    # --- Commands -----

    def _name_filter(self) -> str:
        # {suffix}: the file name ending of a backup, e.g. ".ponosbackup".
        return self.tr("Ponos Coach backup (*{suffix})").format(suffix=BACKUP_SUFFIX)

    def back_up(self) -> None:
        """Ask where to save the backup and start it."""
        profile = self._controller.profile
        if profile is None or self._runner.is_busy:
            return
        start_folder = str(self._settings.value(_KEY_BACKUP_FOLDER, "", type=str))
        name = suggested_backup_name(profile.name.value, self._today())
        start = str(Path(start_folder) / name) if start_folder else name
        chosen = self.pickers.save_file(
            self._window, self.tr("Back up profile"), start, self._name_filter()
        )
        if not chosen:
            return
        path = Path(chosen)
        if path.suffix != BACKUP_SUFFIX:
            path = path.with_name(path.name + BACKUP_SUFFIX)
        self._settings.setValue(_KEY_BACKUP_FOLDER, str(path.parent))
        self._runner.start_backup(profile.folder, path)

    def restore(self) -> None:
        """Ask for the backup, then for the new or empty folder, and start the restore."""
        if self._runner.is_busy:
            return
        start_file = str(self._settings.value(_KEY_RESTORE_FILE_FOLDER, "", type=str))
        chosen = self.pickers.open_file(
            self._window, self.tr("Choose the backup to restore"), start_file, self._name_filter()
        )
        if not chosen:
            return
        source = Path(chosen)
        self._settings.setValue(_KEY_RESTORE_FILE_FOLDER, str(source.parent))
        start_folder = str(self._settings.value(_KEY_RESTORE_FOLDER, "", type=str))
        folder_text = self.pickers.folder(
            self._window, self.tr("Choose a new or empty folder for the profile"), start_folder
        )
        if not folder_text:
            return
        folder = Path(folder_text)
        self._settings.setValue(_KEY_RESTORE_FOLDER, str(folder.parent))
        self._runner.start_restore(source, folder)

    # --- Progress and outcomes -----

    def _on_started(self, kind: object) -> None:
        text = (
            self.tr("Backing up the profile...")
            if kind is BackupKind.BACKUP
            else self.tr("Restoring the profile...")
        )
        self._progress_label.setText(text)
        self._progress_timer.start(self.progress_delay_ms)
        self.refresh()

    def _show_progress(self) -> None:
        for widget in (self._progress_label, self._progress_bar):
            widget.setVisible(True)

    def _on_finished(self, result: object) -> None:
        self._progress_timer.stop()
        for widget in (self._progress_label, self._progress_bar):
            widget.setVisible(False)
        self.refresh()
        match result:
            case BackupFailed(kind=BackupKind.BACKUP, error=error):
                self._show_error(backup_error_message(error), "backupError")
            case BackupFailed(error=error):
                self._show_error(restore_error_message(error), "restoreError")
            case BackupSucceeded(kind=BackupKind.BACKUP) as done:
                self._backup_done(done)
            case BackupSucceeded() as done:
                self._restore_done(done)

    def _backup_done(self, done: BackupSucceeded) -> None:
        box = self._box("backupDone", self.tr("Backup saved"))
        # {file}: the name of the backup file.
        box.setText(self.tr('The backup "{file}" was saved.').format(file=done.target.name))
        box.setInformativeText(
            "\n".join(
                (
                    backup_done_text(done.summary),
                    self.tr(
                        "The file is not encrypted and holds your health data and GPS "
                        "positions (no Garmin password or token): keep it somewhere private, "
                        "not in a folder synchronized with the cloud."
                    ),
                )
            )
        )
        box.setStandardButtons(QMessageBox.StandardButton.Close)
        self._open_box(box)

    def _restore_done(self, done: BackupSucceeded) -> None:
        listed = next(
            (e for e in self._controller.profiles if e.profile_id == done.summary.profile_id),
            None,
        )
        box = self._box("restoreDone", self.tr("Profile restored"))
        # {folder}: the folder the profile was restored into.
        box.setText(self.tr("The profile was restored into {folder}.").format(folder=done.target))
        lines = [restore_done_text(done.summary)]
        if listed is None:
            lines.append(self.tr("Open the restored profile now?"))
            open_text = self.tr("Open profile")
            keep_text = self.tr("Not now")
        else:
            # {name}: the name of the profile already in the list; {folder}: its folder.
            lines.append(
                self.tr(
                    'The list already has a profile with the same id: "{name}" in {folder}. '
                    "Replace its entry with the restored profile and open it? "
                    "The old folder is not deleted."
                ).format(name=listed.cached_name.value, folder=listed.folder)
            )
            open_text = self.tr("Replace entry")
            keep_text = self.tr("Keep the list as it is")
        box.setInformativeText("\n".join(lines))
        open_button = box.addButton(open_text, QMessageBox.ButtonRole.AcceptRole)
        keep_button = box.addButton(keep_text, QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep_button if listed is not None else open_button)
        box.setEscapeButton(keep_button)
        if not self._controller.can_change_profile:
            open_button.setEnabled(False)
            open_button.setToolTip(
                self.tr("Wait for the current operation to finish, or stop it first.")
            )
        box.buttonClicked.connect(
            lambda clicked, b=open_button, f=done.target: self._on_open_answer(clicked, b, f)
        )
        self._open_box(box)

    def _on_open_answer(self, clicked: object, open_button: object, folder: Path) -> None:
        if clicked is open_button:
            self._open_profile_folder(folder)

    def _box(self, name: str, title: str) -> QMessageBox:
        box = QMessageBox(self._window)
        box.setIcon(QMessageBox.Icon.Information)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setObjectName(name)
        return box

    def _open_box(self, box: QMessageBox) -> None:
        self._remember_box(box)
        box.open()
