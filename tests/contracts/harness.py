# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding that each implementation offers to the contract tests.

The ports do not allow creating a fault (a tampered file, an orphan temporary, a file
in a folder): each implementation, fake or real, does it by its own means. This way the same
contract runs on all of them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ponos_coach.activities.domain import ArchivedFile
from ponos_coach.activities.ports import ActivityArchive
from ponos_coach.profiles.domain import FolderKind, ProfileId
from ponos_coach.profiles.ports import ProfileFolders, ProfileRegistry, ProfileStorage, TokenStore


class ArchiveHarness(Protocol):
    """An empty archive and the ways to break it."""

    @property
    def archive(self) -> ActivityArchive:
        """The archive under test."""
        ...

    def plant_orphan_temporary(self) -> None:
        """Leave in the archive a temporary from an interrupted write."""
        ...

    def corrupt(self, file: ArchivedFile) -> None:
        """Alter the bytes of the archived file (same size, different content)."""
        ...

    def delete(self, file: ArchivedFile) -> None:
        """Remove the file from the archive."""
        ...


class RegistryHarness(Protocol):
    """An empty list of profiles."""

    @property
    def registry(self) -> ProfileRegistry:
        """The list under test."""
        ...

    def reopen(self) -> ProfileRegistry:
        """A new object on the same medium (as after an app restart)."""
        ...


class FoldersHarness(Protocol):
    """An existing, empty root folder to test in, and the ways to fill it."""

    @property
    def folders(self) -> ProfileFolders:
        """The folders under test."""
        ...

    @property
    def root(self) -> Path:
        """Existing, empty root folder (absolute)."""
        ...

    @property
    def root_kind(self) -> FolderKind:
        """Kind that `inspect(root)` must report."""
        ...

    def put_file(self, folder: Path, name: str) -> None:
        """Put any file in the existing folder."""
        ...

    def make_profile_folder(self, folder: Path) -> None:
        """Put a profile database in the existing folder (as `create` leaves it)."""
        ...


class StorageHarness(Protocol):
    """The profile database and folders to create them in."""

    @property
    def storage(self) -> ProfileStorage:
        """The database under test."""
        ...

    def new_folder(self) -> Path:
        """A new, existing and empty absolute folder, distinct at each call."""
        ...


class TokenStoreHarness(Protocol):
    """An empty token store and the ways to break it."""

    @property
    def store(self) -> TokenStore:
        """The store under test."""
        ...

    def reopen(self) -> TokenStore:
        """A new object on the same medium (as after an app restart)."""
        ...

    def corrupt(self, profile_id: ProfileId) -> None:
        """Make the content saved for the profile unreadable (it must have one)."""
        ...
