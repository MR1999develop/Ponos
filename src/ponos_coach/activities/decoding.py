# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Single entry point to decode a FIT activity file.

Only the FIT format is accepted: the file name extension must be `.fit` and the reader checks
the content (signature, CRC, structure). No I/O. There is only one error, `ActivityFileError`,
with an enumerated reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ponos_coach.activities.domain import Activity, ActivityTrack, FileFormat
from ponos_coach.activities.fit_decoder import (
    FitDecodeError,
    FitLimits,
    decode_activity,
    decode_track,
)

_FIT_EXTENSION = "fit"


class ActivityFileReason(Enum):
    """Reason why a file does not become an activity. The value is stable."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"


class ActivityFileError(Exception):
    """The file is not a valid activity within the limits; `reason` says why."""

    def __init__(self, reason: ActivityFileReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ActivityFileLimits:
    """Limits of the reader (project default: 50 MB, one million records)."""

    fit: FitLimits = field(default_factory=FitLimits)


def _extension(file_name: str) -> str:
    """Lowercase extension, without the dot, after the last dot in the name (empty if missing)."""
    _, dot, extension = file_name.rpartition(".")
    return extension.lower() if dot else ""


def decode_activity_file(
    data: bytes, file_name: str, limits: ActivityFileLimits | None = None
) -> Activity:
    """Decodes a FIT activity file.

    Of `file_name` only the extension counts (`.fit`, case-insensitive): never a path. The order
    of checks is: empty, extension, then the reader with its limits.

    Raises:
        ActivityFileError: the only error raised. EMPTY; UNSUPPORTED_FORMAT for an extension
            other than `.fit`; then the reader reasons (TOO_LARGE, CORRUPT, NOT_AN_ACTIVITY,
            TOO_MANY_RECORDS, TOO_LONG).
    """
    limits = limits or ActivityFileLimits()
    if not data:
        raise ActivityFileError(ActivityFileReason.EMPTY)
    if _extension(file_name) != _FIT_EXTENSION:
        raise ActivityFileError(ActivityFileReason.UNSUPPORTED_FORMAT)
    return _decode_fit(data, limits)


def decode_stored_activity(
    data: bytes,
    file_format: FileFormat,  # noqa: ARG001 - a single format; kept for the saved data
    limits: ActivityFileLimits | None = None,
) -> Activity:
    """Decodes an already archived file, whose format is known from the index.

    There is no name to compare: the reader performs all the checks on its own content.
    `file_format` is the one registered in the index (today only FIT). No I/O.

    Raises:
        ActivityFileError: the only error raised, as for `decode_activity_file`: EMPTY
            and then the reader reasons (TOO_LARGE, CORRUPT, NOT_AN_ACTIVITY, TOO_MANY_RECORDS,
            TOO_LONG).
    """
    return _decode_fit(data, limits or ActivityFileLimits())


def _decode_fit(data: bytes, limits: ActivityFileLimits) -> Activity:
    """The FIT reader with its limits; its errors become `ActivityFileError`."""
    try:
        return decode_activity(data, limits.fit)
    except FitDecodeError as exc:
        raise ActivityFileError(ActivityFileReason(exc.reason.value)) from exc


def decode_stored_track(
    data: bytes,
    file_format: FileFormat,  # noqa: ARG001 - a single format; kept for the saved data
    limits: ActivityFileLimits | None = None,
) -> ActivityTrack | None:
    """Full track (positions and altitude) of an already archived file, `None` without positions.

    Like `decode_stored_activity`, but it does not touch the load or the HR series. No I/O.

    Raises:
        ActivityFileError: EMPTY, TOO_LARGE, CORRUPT or TOO_MANY_RECORDS.
    """
    limits = limits or ActivityFileLimits()
    try:
        return decode_track(data, limits.fit)
    except FitDecodeError as exc:
        raise ActivityFileError(ActivityFileReason(exc.reason.value)) from exc
