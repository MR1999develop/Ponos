# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile folders on disk: inspection, private creation, lock, deletion."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ponos_coach.platform.file_lock import OsFileLock, acquire_file_lock
from ponos_coach.platform.private_folder import make_private_directory, restrict_directory
from ponos_coach.platform.tree import LinkInTreeError, check_no_links, is_link, remove_children
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    PROFILE_LOCK_NAME,
    PROFILE_OWNED_NAMES,
    FolderInspection,
    FolderKind,
    FolderState,
)
from ponos_coach.profiles.ports import (
    FolderHasOtherContentError,
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    ProfileInUseError,
)


def _require_absolute(folder: Path) -> None:
    if not folder.is_absolute():
        raise FolderUnavailableError("the profile folder must be an absolute path")


def _is_empty(folder: Path) -> bool:
    with os.scandir(folder) as entries:
        return next(entries, None) is None


def _has_profile_database(folder: Path) -> bool:
    """The database is a real file (not a link) in the folder."""
    database = folder / PROFILE_DATABASE_NAME
    try:
        return database.is_file() and not is_link(database)
    except OSError:
        return False


def _only_lock_inside(folder: Path) -> bool:
    """True if the folder holds at most the lock file, and it is not a link."""
    with os.scandir(folder) as entries:
        names = [entry.name for entry in entries]
    if not names:
        return True
    return names == [PROFILE_LOCK_NAME] and not is_link(folder / PROFILE_LOCK_NAME)


def _require_only_owned_names(folder: Path) -> None:
    """The folder contains only names that the app puts in a profile (`PROFILE_OWNED_NAMES`)."""
    with os.scandir(folder) as entries:
        foreign = [entry.name for entry in entries if entry.name not in PROFILE_OWNED_NAMES]
    if foreign:
        raise FolderHasOtherContentError(f"{folder.name} contains other files")


class LocalProfileFolders:
    """`ProfileFolders` on the local file system.

    `kind_of` says where a folder is (network, synchronized…): in production
    `SystemFolderKindDetector(os.environ)`, in tests a fixed value.
    """

    def __init__(self, kind_of: Callable[[Path], FolderKind]) -> None:
        self._kind_of = kind_of

    def inspect(self, folder: Path) -> FolderInspection:
        """See `ProfileFolders.inspect`."""
        _require_absolute(folder)
        kind = self._kind_of(folder)
        try:
            if not folder.exists():
                return FolderInspection(FolderState.MISSING, kind, has_profile_database=False)
            if is_link(folder) or not folder.is_dir():
                raise FolderUnavailableError(f"{folder.name} is not a folder")
            state = FolderState.EMPTY if _is_empty(folder) else FolderState.NOT_EMPTY
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name} is not readable") from exc
        return FolderInspection(state, kind, _has_profile_database(folder))

    def create_private_folder(self, folder: Path) -> None:
        """See `ProfileFolders.create_private_folder`."""
        _require_absolute(folder)
        try:
            if folder.exists():
                self._restrict_existing(folder)
            else:
                self._create_with_parents(folder)
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: creation failed") from exc

    def _restrict_existing(self, folder: Path) -> None:
        if is_link(folder) or not folder.is_dir():
            raise FolderUnavailableError(f"{folder.name} is not a folder")
        if not _is_empty(folder):
            raise FolderNotEmptyError(folder.name)
        restrict_directory(folder)

    def _create_with_parents(self, folder: Path) -> None:
        missing = [p for p in (folder, *folder.parents) if not p.exists()]
        for directory in reversed(missing):  # from the highest to the lowest
            try:
                make_private_directory(directory)
            except FileExistsError:
                if directory == folder or not directory.is_dir():
                    raise

    def acquire_lock(self, folder: Path) -> OsFileLock:
        """See `ProfileFolders.acquire_lock`."""
        _require_absolute(folder)
        if not folder.is_dir():
            raise FolderUnavailableError(f"{folder.name} does not exist")
        return acquire_file_lock(folder / PROFILE_LOCK_NAME)

    def remove_if_empty(self, folder: Path) -> bool:
        """See `ProfileFolders.remove_if_empty`."""
        _require_absolute(folder)
        try:
            if not folder.exists() or is_link(folder) or not folder.is_dir():
                return False
            try:
                lock = self.acquire_lock(folder)
            except ProfileInUseError:
                return False
            try:
                if not _only_lock_inside(folder):
                    return False
                lock.release()  # on Windows an open file cannot be deleted
                (folder / PROFILE_LOCK_NAME).unlink(missing_ok=True)
                folder.rmdir()
                return True
            finally:
                lock.release()
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: removal failed") from exc

    def delete_folder(self, folder: Path) -> None:
        """See `ProfileFolders.delete_folder`.

        Deletes everything except the lock file first, then the database last, finally releases
        the lock and removes the lock file and the folder. A link or a junction in the tree, or
        a name the app does not put in a profile folder, blocks the operation before
        deleting anything.
        """
        _require_absolute(folder)
        if folder.exists() and is_link(folder):
            raise FolderUnavailableError(f"{folder.name} is a link, it is not deleted")
        database = folder / PROFILE_DATABASE_NAME
        if not database.is_file() or is_link(database):
            raise NotAProfileFolderError(folder.name)
        lock = self.acquire_lock(folder)  # ProfileInUseError if open elsewhere
        try:
            self._delete_locked(folder, lock)
        except LinkInTreeError as exc:
            raise FolderUnavailableError(
                f"{folder.name}: contains a link, it is not deleted"
            ) from exc
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: deletion failed") from exc
        finally:
            lock.release()

    def _delete_locked(self, folder: Path, lock: OsFileLock) -> None:
        check_no_links(folder)
        _require_only_owned_names(folder)
        remove_children(
            folder, keep={PROFILE_LOCK_NAME}, last=(PROFILE_DATABASE_NAME,)
        )  # -wal and -shm start like the database
        lock.release()  # on Windows an open file cannot be deleted
        (folder / PROFILE_LOCK_NAME).unlink()
        folder.rmdir()
