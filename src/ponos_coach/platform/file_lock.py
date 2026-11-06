# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lock esclusivo del sistema operativo su un file, tenuto aperto per tutta la vita del profilo.

Il lock è dell'handle aperto, non l'esistenza del file: se il processo muore (anche per un
crash o un kill) il sistema lo rilascia da solo e il file rimasto non blocca nessuno. Due
aperture nello stesso processo si escludono a vicenda (lock per handle, non per processo).
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
        """Blocca il byte 0 senza attendere; `OSError` se è già bloccato."""
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
        """Lock esclusivo non bloccante; `OSError` se è già preso."""
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)

    _OPEN_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    _BUSY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

_LOCK_FILE_MODE = 0o600


class OsFileLock:
    """Lock tenuto da un descrittore aperto. `release` lo rilascia; ripeterlo non fa nulla."""

    def __init__(self, fd: int) -> None:
        self._fd: int | None = fd

    def release(self) -> None:
        """Rilascia il lock e chiude il file (il file resta sul disco)."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            _unlock(fd)
        finally:
            os.close(fd)


def acquire_file_lock(lock_path: Path) -> OsFileLock:
    """Crea se serve il file e prende il lock esclusivo, senza attese.

    Raises:
        ProfileInUseError: il lock è già preso (da un altro processo o da questo).
        FolderUnavailableError: file non apribile (cartella mancante, permessi, link).
    """
    try:
        if lock_path.exists() and is_link(lock_path):
            raise FolderUnavailableError("il file di lock è un link")
        fd = os.open(lock_path, _OPEN_FLAGS, _LOCK_FILE_MODE)
    except OSError as exc:
        raise FolderUnavailableError("file di lock non apribile") from exc
    try:
        _try_lock(fd)
    except OSError as exc:
        os.close(fd)
        if exc.errno in _BUSY_ERRNOS:
            raise ProfileInUseError("profilo già aperto da un'altra istanza") from exc
        raise FolderUnavailableError("lock non ottenibile") from exc
    return OsFileLock(fd)
