# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Domain types of the profiles: id, name, folder kind, registry entry, information."""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

NAME_MAX_CHARS = 60
_NAME_MAX_INPUT_CHARS = 1000  # cap before normalizing: the length is checked right away
_UUID_VERSION = 4
_TIMEZONE_MAX_CHARS = 64


class InvalidProfileNameError(ValueError):
    """The text is not a valid profile name."""


@dataclass(frozen=True, slots=True)
class ProfileId:
    """Identifier of a profile: a random UUID (version 4).

    It is generated outside the domain (`ProfileId(uuid.uuid4())`) or read from text with `parse`.
    """

    value: uuid.UUID

    def __post_init__(self) -> None:
        if self.value.version != _UUID_VERSION or self.value.variant != uuid.RFC_4122:
            raise ValueError("the profile id must be a version 4 UUID")

    @classmethod
    def parse(cls, text: str) -> ProfileId:
        """Id from text in canonical form (36 characters, lowercase, with hyphens).

        Raises:
            ValueError: text of another form or UUID of another version.
        """
        try:
            parsed = uuid.UUID(text)
        except ValueError as exc:
            raise ValueError("invalid profile id") from exc
        if str(parsed) != text:
            raise ValueError("profile id not in canonical form")
        return cls(parsed)

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True)
class ProfileName:
    """Name of a profile: 1-60 characters, Unicode NFC, without outer spaces or
    control or format characters.

    The constructor accepts only already normalized text; `parse` normalizes the user's
    text (NFC, outer spaces removed) and then validates it.
    """

    value: str

    def __post_init__(self) -> None:
        if (
            unicodedata.normalize("NFC", self.value) != self.value
            or self.value != self.value.strip()
        ):
            raise InvalidProfileNameError("the name is not normalized (NFC, no outer spaces)")
        if not 1 <= len(self.value) <= NAME_MAX_CHARS:
            raise InvalidProfileNameError(
                f"the name must have from 1 to {NAME_MAX_CHARS} characters"
            )
        if any(unicodedata.category(char).startswith(("C", "Zl", "Zp")) for char in self.value):
            raise InvalidProfileNameError("the name contains control characters")

    @classmethod
    def parse(cls, text: str) -> ProfileName:
        """Name from the text entered by the user.

        Raises:
            InvalidProfileNameError: empty after removing outer spaces, longer than 60
                characters (after NFC) or with control characters.
        """
        if len(text) > _NAME_MAX_INPUT_CHARS:
            raise InvalidProfileNameError("the name is too long")
        return cls(unicodedata.normalize("NFC", text).strip())

    def __str__(self) -> str:
        return self.value


class FolderKind(Enum):
    """Where a folder is: decides whether to warn the user (network and synchronization are risks
    for SQLite)."""

    LOCAL = "local"
    NETWORK = "network"
    CLOUD_SYNCED = "cloud_synced"
    UNKNOWN = "unknown"


class FolderState(Enum):
    """State of a folder chosen for a profile."""

    MISSING = "missing"
    EMPTY = "empty"
    NOT_EMPTY = "not_empty"


@dataclass(frozen=True, slots=True)
class FolderInspection:
    """Outcome of inspecting a folder: state, kind (for a missing folder, the kind
    of the place where it would be created) and whether it contains a profile's database."""

    state: FolderState
    kind: FolderKind
    has_profile_database: bool


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """Entry of the profile list in the app's configuration.

    `folder` is absolute; `cached_name` is the last known name (the source of truth is the database
    of the profile); `last_used_utc` is the last use, in UTC.
    """

    profile_id: ProfileId
    folder: Path
    cached_name: ProfileName
    last_used_utc: datetime

    def __post_init__(self) -> None:
        if not self.folder.is_absolute():
            raise ValueError("the profile folder must be an absolute path")
        if self.last_used_utc.utcoffset() != timedelta(0):
            raise ValueError("the last use must be a UTC instant with time zone")


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """What the database declares about the profile: id, name, time zone and format version.

    `timezone_name` is an IANA name (e.g. `Europe/Rome`) of at most 64 characters: that it really
    exists
    is verified by the boundaries (`profiles.timezones`), because looking it up requires reading
    the data
    of the time zones and the domain stays free of I/O. `format_version` is the schema version, 1
    or more.
    """

    profile_id: ProfileId
    name: ProfileName
    timezone_name: str
    format_version: int

    def __post_init__(self) -> None:
        if not 0 < len(self.timezone_name) <= _TIMEZONE_MAX_CHARS:
            raise ValueError("time zone name empty or too long")
        if self.format_version < 1:
            raise ValueError("the format version starts at 1")


@dataclass(frozen=True, slots=True)
class ProfilePeek:
    """Read-only glimpse of a profile, without opening or migrating it: information and number
    of activities."""

    info: ProfileInfo
    activity_count: int

    def __post_init__(self) -> None:
        if self.activity_count < 0:
            raise ValueError("the number of activities cannot be negative")
