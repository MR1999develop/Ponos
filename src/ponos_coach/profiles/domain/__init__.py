# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the profiles feature (no I/O, Qt, network or logging)."""

from ponos_coach.profiles.domain.body import (
    HEIGHT_MAX_CM,
    HEIGHT_MIN_CM,
    MIN_AGE_YEARS,
    MIN_BIRTH_DATE,
    WEIGHT_MAX_TENTHS_KG,
    WEIGHT_MIN_TENTHS_KG,
    BirthDate,
    FixedProfileData,
    Height,
    Weight,
    WeightEntry,
)
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
    "HEIGHT_MAX_CM",
    "HEIGHT_MIN_CM",
    "MIN_AGE_YEARS",
    "MIN_BIRTH_DATE",
    "NAME_MAX_CHARS",
    "PROFILE_ARCHIVE_DIR",
    "PROFILE_BACKUP_DIR",
    "PROFILE_DATABASE_NAME",
    "PROFILE_LOCK_NAME",
    "PROFILE_OWNED_NAMES",
    "WEIGHT_MAX_TENTHS_KG",
    "WEIGHT_MIN_TENTHS_KG",
    "BirthDate",
    "FixedProfileData",
    "FolderInspection",
    "FolderKind",
    "FolderState",
    "Height",
    "InvalidProfileNameError",
    "ProfileId",
    "ProfileInfo",
    "ProfileName",
    "ProfilePeek",
    "RegistryEntry",
    "Weight",
    "WeightEntry",
]
