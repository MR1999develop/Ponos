# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cartelle dei profili sul disco: ispezione, creazione privata, lock, cancellazione."""

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
    FolderInspection,
    FolderKind,
    FolderState,
)
from ponos_coach.profiles.ports import (
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    ProfileInUseError,
)


def _require_absolute(folder: Path) -> None:
    if not folder.is_absolute():
        raise FolderUnavailableError("la cartella del profilo deve essere un percorso assoluto")


def _is_empty(folder: Path) -> bool:
    with os.scandir(folder) as entries:
        return next(entries, None) is None


def _has_profile_database(folder: Path) -> bool:
    """Il database è un file vero (non un link) nella cartella."""
    database = folder / PROFILE_DATABASE_NAME
    try:
        return database.is_file() and not is_link(database)
    except OSError:
        return False


def _only_lock_inside(folder: Path) -> bool:
    """Vero se nella cartella c'è al più il file di lock, e questo non è un link."""
    with os.scandir(folder) as entries:
        names = [entry.name for entry in entries]
    if not names:
        return True
    return names == [PROFILE_LOCK_NAME] and not is_link(folder / PROFILE_LOCK_NAME)


class LocalProfileFolders:
    """`ProfileFolders` sul file system locale.

    `kind_of` dice dove sta una cartella (rete, sincronizzata…): in produzione
    `SystemFolderKindDetector(os.environ)`, nei test un valore fisso.
    """

    def __init__(self, kind_of: Callable[[Path], FolderKind]) -> None:
        self._kind_of = kind_of

    def inspect(self, folder: Path) -> FolderInspection:
        """Vedi `ProfileFolders.inspect`."""
        _require_absolute(folder)
        kind = self._kind_of(folder)
        try:
            if not folder.exists():
                return FolderInspection(FolderState.MISSING, kind, has_profile_database=False)
            if is_link(folder) or not folder.is_dir():
                raise FolderUnavailableError(f"{folder.name} non è una cartella")
            state = FolderState.EMPTY if _is_empty(folder) else FolderState.NOT_EMPTY
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name} non è leggibile") from exc
        return FolderInspection(state, kind, _has_profile_database(folder))

    def create_private_folder(self, folder: Path) -> None:
        """Vedi `ProfileFolders.create_private_folder`."""
        _require_absolute(folder)
        try:
            if folder.exists():
                self._restrict_existing(folder)
            else:
                self._create_with_parents(folder)
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: creazione non riuscita") from exc

    def _restrict_existing(self, folder: Path) -> None:
        if is_link(folder) or not folder.is_dir():
            raise FolderUnavailableError(f"{folder.name} non è una cartella")
        if not _is_empty(folder):
            raise FolderNotEmptyError(folder.name)
        restrict_directory(folder)

    def _create_with_parents(self, folder: Path) -> None:
        missing = [p for p in (folder, *folder.parents) if not p.exists()]
        for directory in reversed(missing):  # dal più alto al più basso
            try:
                make_private_directory(directory)
            except FileExistsError:
                if directory == folder or not directory.is_dir():
                    raise

    def acquire_lock(self, folder: Path) -> OsFileLock:
        """Vedi `ProfileFolders.acquire_lock`."""
        _require_absolute(folder)
        if not folder.is_dir():
            raise FolderUnavailableError(f"{folder.name} non esiste")
        return acquire_file_lock(folder / PROFILE_LOCK_NAME)

    def remove_if_empty(self, folder: Path) -> bool:
        """Vedi `ProfileFolders.remove_if_empty`."""
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
                lock.release()  # su Windows un file aperto non si cancella
                (folder / PROFILE_LOCK_NAME).unlink(missing_ok=True)
                folder.rmdir()
                return True
            finally:
                lock.release()
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: rimozione non riuscita") from exc

    def delete_folder(self, folder: Path) -> None:
        """Vedi `ProfileFolders.delete_folder`.

        Cancella prima tutto tranne il file di lock, poi il database per ultimo, infine rilascia
        il lock e toglie il file di lock e la cartella. Un link o una giunzione nell'albero
        blocca l'operazione prima di cancellare qualunque cosa.
        """
        _require_absolute(folder)
        if folder.exists() and is_link(folder):
            raise FolderUnavailableError(f"{folder.name} è un link, non si cancella")
        database = folder / PROFILE_DATABASE_NAME
        if not database.is_file() or is_link(database):
            raise NotAProfileFolderError(folder.name)
        lock = self.acquire_lock(folder)  # ProfileInUseError se aperto altrove
        try:
            self._delete_locked(folder, lock)
        except LinkInTreeError as exc:
            raise FolderUnavailableError(
                f"{folder.name}: contiene un link, non si cancella"
            ) from exc
        except OSError as exc:
            raise FolderUnavailableError(f"{folder.name}: cancellazione non riuscita") from exc
        finally:
            lock.release()

    def _delete_locked(self, folder: Path, lock: OsFileLock) -> None:
        check_no_links(folder)
        remove_children(
            folder, keep={PROFILE_LOCK_NAME}, last=(PROFILE_DATABASE_NAME,)
        )  # -wal e -shm iniziano come il database
        lock.release()  # su Windows un file aperto non si cancella
        (folder / PROFILE_LOCK_NAME).unlink()
        folder.rmdir()
