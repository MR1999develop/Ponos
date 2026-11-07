# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""File format and identity of an activity (PLAN.md section 4.3).

The identity is a union of two cases: a FIT is recognized by its `file_id`, a GPX (which has none)
by the SHA-256 of its bytes. `identity_key` and `identity_from_key` are the only
stable textual representation, for saved data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_FIT_KEY_PREFIX = "fit:"
_GPX_KEY_PREFIX = "gpx:"
_FIT_KEY_PARTS = 4  # manufacturer, product, serial number, creation time


class FileFormat(Enum):
    """Format of an activity's original file. The value is the extension, without the dot."""

    FIT = "fit"
    GPX = "gpx"


@dataclass(frozen=True, slots=True)
class FileId:
    """Identity of a FIT: the `file_id` (manufacturer, product, serial number, time).

    The same for the same file downloaded from Garmin or copied from the watch. `time_created`
    is in UTC.
    """

    manufacturer: int
    product: int
    serial_number: int
    time_created: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.manufacturer <= _UINT16_MAX or not 0 <= self.product <= _UINT16_MAX:
            raise ValueError("manufacturer or product outside uint16")
        if not 0 <= self.serial_number <= _UINT32_MAX:
            raise ValueError("serial number outside uint32")
        if self.time_created.utcoffset() != timedelta(0):
            raise ValueError("time_created must be a UTC instant with time zone")


@dataclass(frozen=True, slots=True)
class FitIdentity:
    """Identity of an activity that arrived as a FIT."""

    file_id: FileId


@dataclass(frozen=True, slots=True)
class GpxIdentity:
    """Identity of an activity that arrived as a GPX: SHA-256 of the file (64 lowercase digits)."""

    sha256_hex: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.sha256_hex):
            raise ValueError("the SHA-256 must be 64 lowercase hexadecimal digits")


ActivityIdentity = FitIdentity | GpxIdentity


def format_of(identity: ActivityIdentity) -> FileFormat:
    """File format the identity belongs to."""
    return FileFormat.FIT if isinstance(identity, FitIdentity) else FileFormat.GPX


def identity_key(identity: ActivityIdentity) -> str:
    """Unique and stable textual key of the identity (for UNIQUE columns and orderings).

    `fit:<manufacturer>:<product>:<serial>:<ISO 8601 UTC time>` or `gpx:<sha256>`.
    """
    if isinstance(identity, GpxIdentity):
        return f"{_GPX_KEY_PREFIX}{identity.sha256_hex}"
    f = identity.file_id
    created = f.time_created.astimezone(UTC).isoformat()
    return f"{_FIT_KEY_PREFIX}{f.manufacturer}:{f.product}:{f.serial_number}:{created}"


def identity_from_key(key: str) -> ActivityIdentity:
    """Inverse of `identity_key`.

    Raises:
        ValueError: key not produced by `identity_key`.
    """
    if key.startswith(_GPX_KEY_PREFIX):
        return GpxIdentity(key.removeprefix(_GPX_KEY_PREFIX))
    if not key.startswith(_FIT_KEY_PREFIX):
        raise ValueError("unknown identity key")
    parts = key.removeprefix(_FIT_KEY_PREFIX).split(":", _FIT_KEY_PARTS - 1)
    if len(parts) != _FIT_KEY_PARTS:
        raise ValueError("incomplete FIT identity key")
    manufacturer, product, serial = (int(part) for part in parts[:3])
    identity = FitIdentity(FileId(manufacturer, product, serial, datetime.fromisoformat(parts[3])))
    if identity_key(identity) != key:
        raise ValueError("non-canonical identity key")
    return identity
