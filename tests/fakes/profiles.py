# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Elenco dei profili e cartelle con lock in memoria (porte `ProfileRegistry`, `ProfileFolders`)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    FolderInspection,
    FolderState,
    ProfileId,
    RegistryEntry,
)
from ponos_coach.profiles.ports import (
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    ProfileAlreadyRegisteredError,
    ProfileInUseError,
    ProfileNotRegisteredError,
)
from tests.fakes.profile_storage import InMemoryDisk


class InMemoryProfileRegistry:
    """Elenco dei profili in un dizionario."""

    def __init__(self) -> None:
        self._entries: dict[ProfileId, RegistryEntry] = {}

    def list_entries(self) -> Sequence[RegistryEntry]:
        """Vedi `ProfileRegistry.list_entries`."""
        return sorted(
            self._entries.values(), key=lambda e: (-e.last_used_utc.timestamp(), str(e.profile_id))
        )

    def add(self, entry: RegistryEntry) -> None:
        """Vedi `ProfileRegistry.add`."""
        same_folder = any(e.folder == entry.folder for e in self._entries.values())
        if entry.profile_id in self._entries or same_folder:
            raise ProfileAlreadyRegisteredError(str(entry.profile_id))
        self._entries[entry.profile_id] = entry

    def remove(self, profile_id: ProfileId) -> None:
        """Vedi `ProfileRegistry.remove`."""
        if self._entries.pop(profile_id, None) is None:
            raise ProfileNotRegisteredError(str(profile_id))

    def mark_used(self, profile_id: ProfileId, when_utc: datetime) -> None:
        """Vedi `ProfileRegistry.mark_used`."""
        entry = self._entries.get(profile_id)
        if entry is None:
            raise ProfileNotRegisteredError(str(profile_id))
        self._entries[profile_id] = replace(entry, last_used_utc=when_utc)


class _Lock:
    def __init__(self, disk: InMemoryDisk, folder: Path) -> None:
        self._disk = disk
        self._folder: Path | None = folder

    def release(self) -> None:
        if self._folder is not None:
            self._disk.locks.discard(self._folder)
            self._folder = None


class InMemoryProfileFolders:
    """`ProfileFolders` su un `InMemoryDisk` (lo stesso del `InMemoryProfileStorage`)."""

    def __init__(self, disk: InMemoryDisk) -> None:
        self.disk = disk

    def inspect(self, folder: Path) -> FolderInspection:
        """Vedi `ProfileFolders.inspect`."""
        names = self.disk.folders.get(folder)
        if names is None:
            state = FolderState.MISSING
        else:
            state = FolderState.NOT_EMPTY if names else FolderState.EMPTY
        return FolderInspection(state, self.disk.kind_of(folder))

    def create_private_folder(self, folder: Path) -> None:
        """Vedi `ProfileFolders.create_private_folder`."""
        if self.disk.folders.get(folder):
            raise FolderNotEmptyError(folder.name)
        self.disk.folders.setdefault(folder, set())

    def acquire_lock(self, folder: Path) -> _Lock:
        """Vedi `ProfileFolders.acquire_lock`."""
        if folder not in self.disk.folders:
            raise FolderUnavailableError(folder.name)
        if folder in self.disk.locks:
            raise ProfileInUseError(folder.name)
        self.disk.locks.add(folder)
        return _Lock(self.disk, folder)

    def delete_folder(self, folder: Path) -> None:
        """Vedi `ProfileFolders.delete_folder`."""
        if PROFILE_DATABASE_NAME not in self.disk.folders.get(folder, set()):
            raise NotAProfileFolderError(folder.name)
        if folder in self.disk.locks:
            raise ProfileInUseError(folder.name)
        del self.disk.folders[folder]
        self.disk.databases.pop(folder, None)
