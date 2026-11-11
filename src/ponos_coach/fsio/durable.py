# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Durable replacement of a file: exclusive temporary, flush, fsync, rename.

Windows: `MoveFileExW` with `MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`, which returns on a
durable name; a directory cannot be flushed with `fsync`, so `sync_directory` does
nothing. POSIX: `os.replace` (atomic), then `fsync` of the directory containing the new name.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Final

_MOVEFILE_REPLACE_EXISTING: Final = 0x1
_MOVEFILE_WRITE_THROUGH: Final = 0x8
_DIR_MODE: Final = 0o700
_REPLACE_ATTEMPTS: Final = 5  # antivirus and indexers cause transient PermissionError
_REPLACE_PAUSE_S: Final = 0.05
_IS_WINDOWS: Final[bool] = sys.platform == "win32"  # bool, not Literal: mypy does not drop a branch


if sys.platform == "win32":

    def _move_file_write_through(source: Path, destination: Path) -> None:
        move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
        move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        move.restype = ctypes.c_int
        flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
        if not move(str(source), str(destination), flags):
            raise ctypes.WinError(ctypes.get_last_error())

else:

    def _move_file_write_through(source: Path, destination: Path) -> None:
        raise NotImplementedError("Windows only")


def replace_file(source: Path, destination: Path) -> None:
    """Renames `source` to `destination` (same volume), replacing the existing one.

    On Windows the new name is already durable on return; on POSIX `sync_directory` is needed. A
    transient `PermissionError` (Windows) is retried a few times, then raised.

    Raises:
        OSError: rename failed; `destination` stays as it was.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            if _IS_WINDOWS:
                _move_file_write_through(source, destination)
            else:
                source.replace(destination)
        except PermissionError:
            if not _IS_WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE_S)
        else:
            return


def sync_directory(folder: Path) -> None:
    """Makes the names in the directory durable (`fsync` on POSIX; on Windows it does nothing)."""
    if _IS_WINDOWS:
        return
    descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_file_atomically(path: Path, data: bytes) -> None:
    """Writes `data` to `path` without ever leaving it half-written.

    Temporary created exclusively (0600) in the same directory, flush, fsync, rename and
    directory synchronization. The previous file stays intact if anything fails. The
    folder must exist.

    Raises:
        OSError: write or rename failed; the temporary is removed.
    """
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_file(temporary, path)
        sync_directory(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def make_subdirectory(path: Path) -> bool:
    """Creates a subfolder (a single level) of an already private folder.

    POSIX: mode 0700. Windows: no mode, so the folder inherits the descriptor of the
    parent folder (Python 3.13's `mkdir(mode=0o700)` for Windows would write a different one).
    True if it created it, false if a folder already existed.

    Raises:
        FileExistsError: exists, but is not a folder.
        OSError: creation failed.
    """
    try:
        if _IS_WINDOWS:
            path.mkdir()
        else:
            path.mkdir(mode=_DIR_MODE)
    except FileExistsError:
        if path.is_dir():
            return False
        raise
    return True
