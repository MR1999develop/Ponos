# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Single entry point to decode an activity file (FIT or GPX).

The format is recognized from the content (the `.FIT` signature of the header, the root element
`gpx`) and must match the file name extension: a `.gpx` that contains a FIT, or the
reverse, is never decoded with the other reader. No I/O. There is only one error,
`ActivityFileError`, with an enumerated reason; the behavior of the two readers does not change.
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
from ponos_coach.activities.gpx_decoder import (
    GpxDecodeError,
    GpxLimits,
    decode_gpx,
    decode_gpx_track,
    read_xml_root,
)

_FIT_SIGNATURE = b".FIT"
_FIT_SIGNATURE_OFFSET = 8
_FIT_HEADER_SIZES = (12, 14)
_GPX_ROOT = "gpx"
_FORMAT_BY_EXTENSION = {"fit": FileFormat.FIT, "gpx": FileFormat.GPX}


class ActivityFileReason(Enum):
    """Reason why a file does not become an activity. The value is stable."""

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    EXTENSION_MISMATCH = "extension_mismatch"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"


class ActivityFileError(Exception):
    """The file is not a valid activity within the limits; `reason` says why."""

    def __init__(self, reason: ActivityFileReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ActivityFileLimits:
    """Limits of the two readers (project default: 50 MB, one million records or points)."""

    fit: FitLimits = field(default_factory=FitLimits)
    gpx: GpxLimits = field(default_factory=GpxLimits)


class DetectedContent(Enum):
    """What the content of a file looks like, regardless of its name."""

    FIT = "fit"
    GPX = "gpx"
    OTHER_XML = "other_xml"  # e.g. TCX: recognized but not supported
    UNKNOWN = "unknown"


def detect_content(data: bytes) -> DetectedContent:
    """Recognizes the format from the content: FIT signature, `gpx` XML root, other XML, unknown.

    Looks only at the start of the file; does not decode it or verify its validity.
    """
    start = _FIT_SIGNATURE_OFFSET
    if (
        len(data) >= start + len(_FIT_SIGNATURE)
        and data[0] in _FIT_HEADER_SIZES
        and data[start : start + len(_FIT_SIGNATURE)] == _FIT_SIGNATURE
    ):
        return DetectedContent.FIT
    root = read_xml_root(data)
    if root is None:
        return DetectedContent.UNKNOWN
    return DetectedContent.GPX if root.name == _GPX_ROOT else DetectedContent.OTHER_XML


def _extension(file_name: str) -> str:
    """Lowercase extension, without the dot, after the last dot in the name (empty if missing)."""
    _, dot, extension = file_name.rpartition(".")
    return extension.lower() if dot else ""


def _expected_format(file_name: str) -> FileFormat:
    expected = _FORMAT_BY_EXTENSION.get(_extension(file_name))
    if expected is None:
        raise ActivityFileError(ActivityFileReason.UNSUPPORTED_FORMAT)
    return expected


def _check_content(data: bytes, expected: FileFormat) -> None:
    detected = detect_content(data)
    if detected is DetectedContent.OTHER_XML:
        raise ActivityFileError(ActivityFileReason.UNSUPPORTED_FORMAT)
    if detected is DetectedContent.UNKNOWN:
        raise ActivityFileError(ActivityFileReason.CORRUPT)
    if detected.value != expected.value:
        raise ActivityFileError(ActivityFileReason.EXTENSION_MISMATCH)


def decode_activity_file(
    data: bytes, file_name: str, limits: ActivityFileLimits | None = None
) -> Activity:
    """Decodes a FIT or GPX activity file.

    Of `file_name` only the extension counts (`.fit` or `.gpx`, case-insensitive):
    never a path. The order of checks is: empty, extension, content recognized and
    consistent with the extension, then the format reader with its limits.

    Raises:
        ActivityFileError: the only error raised. EMPTY; UNSUPPORTED_FORMAT for an extension
            other than `.fit` and `.gpx` or for an XML that is not GPX (e.g. TCX); CORRUPT if
            the content is neither FIT nor allowed XML; EXTENSION_MISMATCH if content and
            extension are different formats; then the reader reasons (TOO_LARGE, CORRUPT,
            NOT_AN_ACTIVITY, TOO_MANY_RECORDS, TOO_LONG).
    """
    limits = limits or ActivityFileLimits()
    if not data:
        raise ActivityFileError(ActivityFileReason.EMPTY)
    expected = _expected_format(file_name)
    _check_content(data, expected)
    return _decode_as(data, expected, limits)


def decode_stored_activity(
    data: bytes, file_format: FileFormat, limits: ActivityFileLimits | None = None
) -> Activity:
    """Decodes an already archived file, whose format is known from the index.

    There is no name to compare: the reader of the given format performs all the checks on its
    own content. No I/O.

    Raises:
        ActivityFileError: the only error raised, as for `decode_activity_file`: EMPTY
            and then the reader reasons (TOO_LARGE, CORRUPT, NOT_AN_ACTIVITY, TOO_MANY_RECORDS,
            TOO_LONG).
    """
    return _decode_as(data, file_format, limits or ActivityFileLimits())


def _decode_as(data: bytes, file_format: FileFormat, limits: ActivityFileLimits) -> Activity:
    """The format reader with its limits; its errors become `ActivityFileError`."""
    try:
        if file_format is FileFormat.FIT:
            return decode_activity(data, limits.fit)
        return decode_gpx(data, limits.gpx)
    except (FitDecodeError, GpxDecodeError) as exc:
        raise ActivityFileError(ActivityFileReason(exc.reason.value)) from exc


def decode_stored_track(
    data: bytes, file_format: FileFormat, limits: ActivityFileLimits | None = None
) -> ActivityTrack | None:
    """Traccia completa (posizioni e quota) di un file già archiviato, o `None` senza posizioni.

    Come `decode_stored_activity`, ma non tocca il carico né la serie di FC. Nessun I/O.

    Raises:
        ActivityFileError: EMPTY, TOO_LARGE, CORRUPT o TOO_MANY_RECORDS.
    """
    limits = limits or ActivityFileLimits()
    try:
        if file_format is FileFormat.FIT:
            return decode_track(data, limits.fit)
        return decode_gpx_track(data, limits.gpx)
    except (FitDecodeError, GpxDecodeError) as exc:
        raise ActivityFileError(ActivityFileReason(exc.reason.value)) from exc
