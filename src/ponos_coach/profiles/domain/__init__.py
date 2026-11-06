# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the profiles feature (no I/O, Qt, network or logging)."""

from ponos_coach.profiles.domain.layout import (
    PROFILE_ARCHIVE_DIR,
    PROFILE_BACKUP_DIR,
    PROFILE_DATABASE_NAME,
    PROFILE_LOCK_NAME,
    PROFILE_OWNED_NAMES,
)
from ponos_coach.profiles.domain.profile import (
    NAME_MAX_CHARS,
    FolderInspection,
    FolderKind,
    FolderState,
    InvalidProfileNameError,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
    RegistryEntry,
)

__all__ = [
    "NAME_MAX_CHARS",
    "PROFILE_ARCHIVE_DIR",
    "PROFILE_BACKUP_DIR",
    "PROFILE_DATABASE_NAME",
    "PROFILE_LOCK_NAME",
    "PROFILE_OWNED_NAMES",
    "FolderInspection",
    "FolderKind",
    "FolderState",
    "InvalidProfileNameError",
    "ProfileId",
    "ProfileInfo",
    "ProfileName",
    "ProfilePeek",
    "RegistryEntry",
]
