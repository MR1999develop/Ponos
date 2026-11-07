# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Safe reading of untrusted ZIP archives, in memory (PLAN.md 4.9).

Entries and declared sizes are checked before extracting (central directory read with a
cap, declared and actual number of entries, sum of sizes, compression ratio per
entry, size per entry); the real size is rechecked during block extraction and
the CRC is verified up to the last byte. Encrypted entries, nested ZIPs and unsafe names are
rejected; entry names are never paths and are not used for writing. Every rejection is
a `ZipReadError` with the reason, never a repaired ZIP.

Low-level module: it imports nothing from the project.
"""

from __future__ import annotations

import io
import os
import struct
import zipfile
import zlib
from dataclasses import dataclass, fields
from enum import Enum
from typing import BinaryIO, Final

from ponos_coach.zipread.names import file_suffix, is_safe_entry_name

ACTIVITY_SUFFIXES: Final = (".fit", ".gpx")
ZIP_SUFFIX: Final = ".zip"

_MIB = 1024 * 1024
_GIB = 1024 * _MIB
_BLOCK_BYTES = 64 * 1024
_ZIP_STORED, _ZIP_DEFLATED, _ZIP_AES = 0, 8, 99
_ZIP_ENCRYPTED_FLAGS = 0x1 | 0x40 | 0x2000  # encrypted, strong encryption, masked directory

# Central directory and end record of the ZIP.
_EOCD = struct.Struct("<4sHHHHIIH")
_EOCD_SIGNATURE = b"PK\x05\x06"
_EOCD_MAX_COMMENT = 0xFFFF
_ZIP64_LOCATOR = struct.Struct("<4sIQI")
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_ZIP64_RECORD = struct.Struct("<4sQHHIIQQQQ")
_ZIP64_RECORD_SIGNATURE = b"PK\x06\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF

_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    OSError,
    ValueError,
    EOFError,
    OverflowError,
    NotImplementedError,
    RuntimeError,
    zlib.error,
    struct.error,
)


class ZipReason(Enum):
    """Why a ZIP or one of its entries cannot be read. The value is stable."""

    ARCHIVE_INVALID = "archive_invalid"  # corrupt, inconsistent, false size or CRC
    ARCHIVE_LIMIT = "archive_limit"  # too many entries, too many bytes, compression ratio
    ENCRYPTED = "encrypted"  # encrypted entry
    UNSAFE_NAME = "unsafe_name"  # `..`, absolute path, drive, control characters, reserved name
    TOO_LARGE = "too_large"  # entry beyond `max_entry_bytes`
    NESTED_ARCHIVE = "nested_archive"  # ZIP inside a ZIP
    NO_ACTIVITY = "no_activity"  # no `.fit` or `.gpx` entry
    MULTIPLE_ACTIVITIES = "multiple_activities"  # more than one `.fit` or `.gpx` entry


class ZipReadError(Exception):
    """The ZIP or the entry cannot be read; `reason` says why. The message is only the reason."""

    def __init__(self, reason: ZipReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ZipLimits:
    """Reading caps; all positive. The defaults are the project's (PLAN.md 4.9)."""

    max_entries: int = 50_000  # entries of a single ZIP
    max_total_bytes: int = 5 * _GIB  # sum of the declared sizes
    max_ratio: int = 100  # declared size / compressed size, per entry
    max_directory_bytes: int = 32 * _MIB  # central directory, read into memory
    max_entry_bytes: int = 50 * _MIB  # declared size of an entry

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"the limit {item.name} must be positive")


@dataclass(frozen=True, slots=True)
class ZipActivity:
    """The extracted activity entry: `suffix` is `.fit` or `.gpx`; the entry name does not leave."""

    suffix: str
    content: bytes


# --- Central directory ----------------------------------------------------------------


def _locate_zip64(handle: BinaryIO, end_position: int) -> tuple[int, int, int]:
    """Entries, bytes and start of the central directory from a ZIP64 record."""
    record_position = end_position - _ZIP64_LOCATOR.size - _ZIP64_RECORD.size
    if record_position < 0:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    handle.seek(record_position)
    raw = handle.read(_ZIP64_RECORD.size + _ZIP64_LOCATOR.size)
    if len(raw) != _ZIP64_RECORD.size + _ZIP64_LOCATOR.size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    record = _ZIP64_RECORD.unpack_from(raw)
    locator = _ZIP64_LOCATOR.unpack_from(raw, _ZIP64_RECORD.size)
    if record[0] != _ZIP64_RECORD_SIGNATURE or locator[0] != _ZIP64_LOCATOR_SIGNATURE:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    total, directory_bytes = record[7], record[8]
    return total, directory_bytes, record_position - directory_bytes


def _locate_directory(handle: BinaryIO, file_size: int) -> tuple[int, int, int]:
    """(declared entries, bytes, position) of the central directory, from the end record."""
    tail_length = min(file_size, _EOCD.size + _EOCD_MAX_COMMENT)
    handle.seek(file_size - tail_length)
    tail = handle.read(tail_length)
    found = tail.rfind(_EOCD_SIGNATURE)
    if found < 0 or len(tail) - found < _EOCD.size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    values = _EOCD.unpack_from(tail, found)
    _, disk, directory_disk, on_disk, total, directory_bytes, directory_offset, _ = values
    end_position = file_size - tail_length + found
    if disk or directory_disk or on_disk != total:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)  # archive spanning several disks
    if _UINT16_MAX in (total, on_disk) or _UINT32_MAX in (directory_bytes, directory_offset):
        return _locate_zip64(handle, end_position)
    return total, directory_bytes, end_position - directory_bytes


def _count_directory_entries(raw: bytes, limit: int) -> int:
    """Entries of the central directory counted on the bytes themselves; beyond `limit` it stops."""
    position = count = 0
    while position < len(raw):
        if raw[position : position + 4] != _CENTRAL_SIGNATURE or position + 46 > len(raw):
            raise ZipReadError(ZipReason.ARCHIVE_INVALID)
        name_bytes, extra_bytes, comment_bytes = struct.unpack_from("<HHH", raw, position + 28)
        position += 46 + name_bytes + extra_bytes + comment_bytes
        count += 1
        if count > limit:
            raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    if position != len(raw):
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return count


def _check_directory(handle: BinaryIO, limits: ZipLimits) -> int:
    """Checks the central directory before letting `zipfile` read it: size,
    declared and actual number of entries. Returns the number of entries."""
    file_size = handle.seek(0, os.SEEK_END)
    declared, directory_bytes, start = _locate_directory(handle, file_size)
    if declared > limits.max_entries or directory_bytes > limits.max_directory_bytes:
        raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    if start < 0:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    handle.seek(start)
    raw = handle.read(directory_bytes)
    if len(raw) != directory_bytes:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    if _count_directory_entries(raw, limits.max_entries) != declared:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return declared


# --- Interface ------------------------------------------------------------------------


def open_checked_zip(handle: BinaryIO, limits: ZipLimits) -> zipfile.ZipFile:
    """Opens the ZIP after checking directory, number of entries, sum of declared
    sizes and duplicate names (even by case only). The caller closes the archive and
    l'handle.

    Raises:
        ZipReadError: `ARCHIVE_INVALID` o `ARCHIVE_LIMIT`.
    """
    try:
        declared = _check_directory(handle, limits)
        archive = zipfile.ZipFile(handle)
    except _ZIP_ERRORS as exc:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID) from exc
    infos = archive.infolist()
    names = {info.filename.lower() for info in infos}
    if len(infos) != declared or len(names) != len(infos):
        archive.close()
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    if sum(info.file_size for info in infos) > limits.max_total_bytes:
        archive.close()
        raise ZipReadError(ZipReason.ARCHIVE_LIMIT)
    return archive


def entry_issue(info: zipfile.ZipInfo, limits: ZipLimits) -> ZipReason | None:
    """Why an entry is not extracted (None if fine), from declared data."""
    checks = (
        (not is_safe_entry_name(info.filename), ZipReason.UNSAFE_NAME),
        (
            bool(info.flag_bits & _ZIP_ENCRYPTED_FLAGS) or info.compress_type == _ZIP_AES,
            ZipReason.ENCRYPTED,
        ),
        (info.compress_type not in (_ZIP_STORED, _ZIP_DEFLATED), ZipReason.ARCHIVE_INVALID),
        (info.file_size > limits.max_entry_bytes, ZipReason.TOO_LARGE),
        (info.file_size > limits.max_ratio * info.compress_size, ZipReason.ARCHIVE_LIMIT),
    )
    return next((issue for failed, issue in checks if failed), None)


def extract_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Extracts the entry in memory in blocks; the real size must be the declared one.

    Raises:
        ZipReadError: `ARCHIVE_INVALID` (wrong CRC, corrupt deflate, truncated entry or with
            size different from the declared one).
    """
    chunks: list[bytes] = []
    total = 0
    try:
        with archive.open(info) as stream:
            while chunk := stream.read(_BLOCK_BYTES):
                total += len(chunk)
                if total > info.file_size:
                    raise ZipReadError(ZipReason.ARCHIVE_INVALID)
                chunks.append(chunk)
    except _ZIP_ERRORS as exc:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID) from exc
    if total != info.file_size:
        raise ZipReadError(ZipReason.ARCHIVE_INVALID)
    return b"".join(chunks)


def _single_activity(archive: zipfile.ZipFile) -> zipfile.ZipInfo:
    """The only activity entry of the archive; a nested ZIP is rejected."""
    candidates: list[zipfile.ZipInfo] = []
    for info in archive.infolist():
        suffix = file_suffix(info.filename)
        if info.is_dir() or suffix not in (*ACTIVITY_SUFFIXES, ZIP_SUFFIX):
            continue
        if suffix == ZIP_SUFFIX:
            raise ZipReadError(ZipReason.NESTED_ARCHIVE)
        candidates.append(info)
    if not candidates:
        raise ZipReadError(ZipReason.NO_ACTIVITY)
    if len(candidates) > 1:
        raise ZipReadError(ZipReason.MULTIPLE_ACTIVITIES)
    return candidates[0]


def read_activity_entry(content: bytes, limits: ZipLimits | None = None) -> ZipActivity:
    """The only `.fit` or `.gpx` entry of a ZIP given as bytes, extracted in memory.

    The other entries (which are not ZIPs) are ignored without reading them. No name leaves here.

    Raises:
        ZipReadError: the ZIP cannot be opened or exceeds a cap, has a nested ZIP, no activity
            entry or more than one, or the entry is encrypted, has an unsafe name, is too
            large, has an excessive compression ratio or inconsistent data.
    """
    effective = limits or ZipLimits()
    with io.BytesIO(content) as handle, open_checked_zip(handle, effective) as archive:
        info = _single_activity(archive)
        issue = entry_issue(info, effective)
        if issue is not None:
            raise ZipReadError(issue)
        return ZipActivity(file_suffix(info.filename), extract_entry(archive, info))
