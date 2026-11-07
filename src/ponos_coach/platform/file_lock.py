# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Exclusive operating-system lock on a file, kept open for the whole life of the profile.

The lock belongs to the open handle, not to the file's existence: if the process dies (even by a
crash or a kill) the system releases it on its own and the leftover file blocks no one. Two
openings in the same process exclude each other (lock per handle, not per process).
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

from ponos_coach.platform.tree import is_link
from ponos_coach.profiles.ports import FolderUnavailableError, ProfileInUseError

if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> None:
        """Locks byte 0 without waiting; `OSError` if it is already locked."""
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | os.O_BINARY
    _BUSY_ERRNOS = frozenset({errno.EACCES, errno.EDEADLK, errno.EAGAIN})
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        """Exclusive non-blocking lock; `OSError` if it is already taken."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    _BUSY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

_LOCK_FILE_MODE = 0o600


class OsFileLock:
    """Lock held by an open descriptor. `release` releases it; repeating it does nothing."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        """Releases the lock and closes the file (the file stays on disk)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)


def acquire_file_lock(lock_path: Path) -> OsFileLock:
    """Creates the file if needed and takes the exclusive lock, without waiting.

    Raises:
        ProfileInUseError: the lock is already taken (by another process or by this one).
        FolderUnavailableError: file cannot be opened (missing folder, permissions, link).
    """
    try:
        if lock_path.exists() and is_link(lock_path):
            raise FolderUnavailableError("the lock file is a link")
        fd = os.open(lock_path, _OPEN_FLAGS, _LOCK_FILE_MODE)
    except OSError as exc:
        raise FolderUnavailableError("lock file cannot be opened") from exc
    try:
        _try_lock(fd)
    except OSError as exc:
        os.close(fd)
        if exc.errno in _BUSY_ERRNOS:
            raise ProfileInUseError("profile already open in another instance") from exc
        raise FolderUnavailableError("lock cannot be obtained") from exc
    return OsFileLock(fd)
