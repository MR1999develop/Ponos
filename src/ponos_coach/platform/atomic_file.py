# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reading with a size cap and atomic writing of small configuration files.

Durable writing lives in `ponos_coach.fsio`, shared with the archive."""

from __future__ import annotations

from pathlib import Path

from ponos_coach.fsio import write_file_atomically


class FileTooLargeError(Exception):
    """The file exceeds the size cap: it is not read beyond."""


def read_limited(path: Path, max_bytes: int) -> bytes | None:
    """Content of the file, or None if it does not exist.

    At most `max_bytes + 1` bytes are read, never the whole file if it is larger.

    Raises:
        FileTooLargeError: the file has more than `max_bytes` bytes.
        OSError: reading impossible (permissions, I/O, it is a folder).
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except FileNotFoundError:
        return None
    if len(data) > max_bytes:
        raise FileTooLargeError(path.name)
    return data


def write_atomic(path: Path, data: bytes) -> None:
    """Writes `data` to `path` without ever leaving it half-written (see
    `fsio.write_file_atomically`).

    The previous file stays intact if anything fails. The folder must exist.

    Raises:
        OSError: write or rename failed; the temporary is removed.
    """
    write_file_atomically(path, data)
