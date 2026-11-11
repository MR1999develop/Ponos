# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Runs a profile backup or restore off the GUI thread, one at a time.

Each job gets its own `DataThread`, which closes by itself when the job is over. The use cases do
not touch the open profile's connection (the backup copies the database as a snapshot), so the
profile can stay open and the window usable. Only the types of errors reach the log.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ponos_coach.profiles.backup import BackupProfile, RestoreProfile
from ponos_coach.profiles.domain.backup import BackupSummary
from ponos_coach.ui.data_thread import (
    DataThread,
    Job,
    JobCancelled,
    JobContext,
    JobFailed,
    JobOutcome,
    JobSucceeded,
)


class BackupKind(Enum):
    """What the job does."""

    BACKUP = "backup"
    RESTORE = "restore"


@dataclass(frozen=True, slots=True)
class BackupUseCases:
    """The use cases the interface calls, built by the Composition Root."""

    backup: BackupProfile
    restore: RestoreProfile


@dataclass(frozen=True, slots=True)
class BackupSucceeded:
    """The job is done; `target` is the backup file or the restored folder."""

    kind: BackupKind
    target: Path
    summary: BackupSummary


@dataclass(frozen=True, slots=True)
class BackupFailed:
    """The job failed; nothing was left behind."""

    kind: BackupKind
    error: Exception


type BackupResult = BackupSucceeded | BackupFailed


class BackupRunner(QObject):
    """Starts a backup or a restore and tells how it ended."""

    started = Signal(object)  # BackupKind
    finished = Signal(object)  # BackupResult

    def __init__(self, use_cases: BackupUseCases, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._use_cases = use_cases
        self._thread: DataThread | None = None

    @property
    def use_cases(self) -> BackupUseCases:
        """The use cases the next job calls."""
        return self._use_cases

    @use_cases.setter
    def use_cases(self, value: BackupUseCases) -> None:
        self._use_cases = value

    @property
    def is_busy(self) -> bool:
        """True from the start of a job until its outcome has been signalled."""
        return self._thread is not None

    def start_backup(self, folder: Path, destination: Path) -> bool:
        """Back up the profile in `folder` to the new file `destination`; false if busy."""
        use_case = self._use_cases.backup

        def job(_context: JobContext) -> BackupSummary:
            return use_case(folder, destination)

        return self._start(BackupKind.BACKUP, destination, job)

    def start_restore(self, source: Path, folder: Path) -> bool:
        """Restore the backup `source` into the new or empty `folder`; false if busy."""
        use_case = self._use_cases.restore

        def job(_context: JobContext) -> BackupSummary:
            return use_case(source, folder)

        return self._start(BackupKind.RESTORE, folder, job)

    def _start(self, kind: BackupKind, target: Path, job: Job) -> bool:
        if self._thread is not None:
            return False
        thread = DataThread(lambda: None)
        self._thread = thread
        thread.completed.connect(lambda _id, outcome: self._on_completed(kind, target, outcome))
        self.started.emit(kind)
        thread.submit(job)
        return True

    def _on_completed(self, kind: BackupKind, target: Path, outcome: JobOutcome) -> None:
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.request_shutdown()  # the thread ends by itself; nothing else is queued
        result: BackupResult
        match outcome:
            case JobSucceeded(value=BackupSummary() as summary):
                result = BackupSucceeded(kind, target, summary)
            case JobFailed(error=error):
                result = BackupFailed(kind, error)
            case JobCancelled() | JobSucceeded():
                result = BackupFailed(kind, RuntimeError("the job ended without a result"))
        self.finished.emit(result)
