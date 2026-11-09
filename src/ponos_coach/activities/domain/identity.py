# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""File format and identity of an activity (PLAN.md section 4.3).

The identity of a FIT is its `file_id`. `identity_key` and `identity_from_key` are the only
stable textual representation, for saved data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF
_FIT_KEY_PREFIX = "fit:"
_FIT_KEY_PARTS = 4  # manufacturer, product, serial number, creation time


class FileFormat(Enum):
    """Format of an activity's original file. The value is the extension, without the dot."""

    FIT = "fit"


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


ActivityIdentity = FitIdentity


def format_of(identity: ActivityIdentity) -> FileFormat:  # noqa: ARG001 - a single format
    """File format the identity belongs to."""
    return FileFormat.FIT


def identity_key(identity: ActivityIdentity) -> str:
    """Unique and stable textual key of the identity (for UNIQUE columns and orderings).

    `fit:<manufacturer>:<product>:<serial>:<ISO 8601 UTC time>`.
    """
    f = identity.file_id
    created = f.time_created.astimezone(UTC).isoformat()
    return f"{_FIT_KEY_PREFIX}{f.manufacturer}:{f.product}:{f.serial_number}:{created}"


def identity_from_key(key: str) -> ActivityIdentity:
    """Inverse of `identity_key`.

    Raises:
        ValueError: key not produced by `identity_key`.
    """
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
