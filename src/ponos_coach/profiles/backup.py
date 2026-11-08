# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases `BackupProfile` and `RestoreProfile`: a profile in a versioned backup file.

The format is in `profiles.domain.backup`; reading and writing the file is the job of a
`ProfileBackupStorage`. The use cases decide what is allowed: a backup never overwrites a file
or lands inside the profile it copies, a restore only ever goes to a new folder (never over an
existing profile) and leaves nothing behind when it fails.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ponos_coach.profiles.domain import FolderState
from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX, BackupSummary
from ponos_coach.profiles.ports import (
    FolderNotEmptyError,
    ProfileBackupStorage,
    ProfileFolders,
)


class BackupProfile:
    """Writes a backup of an open profile to a new file."""

    def __init__(
        self,
        storage: ProfileBackupStorage,
        now_utc: Callable[[], datetime],
        app_version: str,
    ) -> None:
        self._storage = storage
        self._now_utc = now_utc
        self._app_version = app_version

    def __call__(self, folder: Path, destination: Path) -> BackupSummary:
        """Backs up the profile in `folder` to `destination` (suffix `.ponosbackup`).

        The profile can stay open: the database is copied as a consistent snapshot. Garmin tokens
        are not part of it (they are not in the profile folder).

        Raises:
            ValueError: `destination` is relative, has another suffix or is inside `folder`
                (nothing was touched).
            BackupFileExistsError: `destination` exists.
            BackupWriteError: reading the profile or writing the file failed; no file is left.
        """
        if not destination.is_absolute() or not folder.is_absolute():
            raise ValueError("paths must be absolute")
        if destination.suffix != BACKUP_SUFFIX:
            raise ValueError(f"a backup file ends with {BACKUP_SUFFIX}")
        if destination.is_relative_to(folder):
            raise ValueError("the backup cannot be written inside the profile folder")
        return self._storage.write(folder, destination, self._now_utc(), self._app_version)


class RestoreProfile:
    """Restores a backup into a new profile folder."""

    def __init__(self, storage: ProfileBackupStorage, folders: ProfileFolders) -> None:
        self._storage = storage
        self._folders = folders

    def __call__(self, source: Path, folder: Path) -> BackupSummary:
        """Restores the backup `source` into `folder`, which is created or must be empty.

        The restored profile keeps its id and is not registered in the profile list: whoever
        calls opens it from its folder afterwards. If anything fails the folder is removed when
        empty and only what this call wrote is deleted.

        Raises:
            ValueError: a path is relative (nothing was touched).
            FolderNotEmptyError: `folder` exists and is not empty; nothing was written.
            FolderUnavailableError: the folder cannot be created or locked.
            ProfileInUseError: the folder is locked by another instance.
            BackupInvalidError: `source` is not a restorable backup.
            BackupTooNewError: the backup is newer than this app knows.
            BackupWriteError: reading `source` or writing `folder` failed.
        """
        if not source.is_absolute() or not folder.is_absolute():
            raise ValueError("paths must be absolute")
        if self._folders.inspect(folder).state is FolderState.NOT_EMPTY:
            raise FolderNotEmptyError(folder.name)
        self._folders.create_private_folder(folder)
        lock = self._folders.acquire_lock(folder)
        try:
            return self._storage.restore(source, folder)
        except BaseException:
            lock.release()
            self._folders.remove_if_empty(folder)
            raise
        finally:
            lock.release()  # idempotent
