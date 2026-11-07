# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Elementary archive write operations: the seam for fault injection.

`FileArchive` writes only through `WriteOps`; tests replace it with a version that
fails or "dies" at the Nth operation (T-DATI-02).

Durability. On all systems: temporary in the same folder as the final file, `fsync` of the
temporary before the rename. Rename and directory synchronization (also of the parent
if the year subfolder is new) live in `ponos_coach.fsio`, shared with the profile
list; on new folders Windows inherits the private descriptor of the profile folder.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, Protocol

from ponos_coach.fsio import make_subdirectory, replace_file, sync_directory

_FILE_MODE: Final = 0o600
_CREATE_FLAGS: Final = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class WriteOps(Protocol):
    """Operations with which the archive writes a file durably, in order of use."""

    def make_dir(self, path: Path) -> bool:
        """Creates the folder (private); true if it created it, false if it already existed."""
        ...

    def create_exclusive(self, path: Path) -> int:
        """Creates a new write-only file (fails if it exists); returns the descriptor."""
        ...

    def write(self, descriptor: int, data: memoryview) -> int:
        """Writes at most `len(data)` bytes; returns how many it wrote."""
        ...

    def fsync(self, descriptor: int) -> None:
        """Flushes the file's data to disk."""
        ...

    def close(self, descriptor: int) -> None:
        """Closes the descriptor."""
        ...

    def replace(self, source: Path, destination: Path) -> None:
        """Renames `source` to `destination` (same volume), replacing the existing one."""
        ...

    def sync_dir(self, path: Path) -> None:
        """Makes the folder's content (the names) durable, where the system allows it."""
        ...

    def unlink(self, path: Path) -> None:
        """Removes the file (no error if missing)."""
        ...


class OsWriteOps:
    """Real implementation on `os` (and on `MoveFileExW` for the rename on Windows)."""

    def make_dir(self, path: Path) -> bool:
        """See `WriteOps.make_dir`."""
        return make_subdirectory(path)

    def create_exclusive(self, path: Path) -> int:
        """See `WriteOps.create_exclusive`: 0600 permissions on POSIX, never following links."""
        return os.open(path, _CREATE_FLAGS, _FILE_MODE)

    def write(self, descriptor: int, data: memoryview) -> int:
        """See `WriteOps.write`."""
        return os.write(descriptor, data)

    def fsync(self, descriptor: int) -> None:
        """See `WriteOps.fsync` (`FlushFileBuffers` on Windows)."""
        os.fsync(descriptor)

    def close(self, descriptor: int) -> None:
        """See `WriteOps.close`."""
        os.close(descriptor)

    def replace(self, source: Path, destination: Path) -> None:
        """See `WriteOps.replace`: with write-through on Windows."""
        replace_file(source, destination)

    def sync_dir(self, path: Path) -> None:
        """See `WriteOps.sync_dir`: `fsync` of the folder on POSIX, nothing on Windows."""
        sync_directory(path)

    def unlink(self, path: Path) -> None:
        """See `WriteOps.unlink`."""
        path.unlink(missing_ok=True)
