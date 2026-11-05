# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura con tetto di dimensione e scrittura atomica di piccoli file di configurazione."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

_REPLACE_ATTEMPTS = 5  # antivirus e indicizzatori danno PermissionError transitori su Windows
_REPLACE_PAUSE_S = 0.05
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo


class FileTooLargeError(Exception):
    """Il file supera il tetto di dimensione: non si legge oltre."""


def read_limited(path: Path, max_bytes: int) -> bytes | None:
    """Contenuto del file, o None se non esiste.

    Si leggono al massimo `max_bytes + 1` byte, mai l'intero file se è più grande.

    Raises:
        FileTooLargeError: il file ha più di `max_bytes` byte.
        OSError: lettura impossibile (permessi, I/O, è una cartella).
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
    """Scrive `data` in `path` senza mai lasciarlo a metà.

    Temporaneo nella stessa cartella, flush, fsync, rinomina; su POSIX anche fsync della
    cartella. Il file di prima resta intatto se qualcosa fallisce. La cartella deve esistere.

    Raises:
        OSError: scrittura o rinomina non riuscite; il temporaneo viene tolto.
    """
    fd, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_limited_retries(temporary, path)
        _sync_folder(path.parent)
    except BaseException:
        with contextlib.suppress(OSError):
            temporary.unlink()
        raise


def _replace_with_limited_retries(source: Path, destination: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            source.replace(destination)
        except PermissionError:
            if not _IS_WINDOWS or attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_PAUSE_S)
        else:
            return


def _sync_folder(folder: Path) -> None:
    """fsync della cartella: il nome nuovo sopravvive a un'interruzione (solo POSIX)."""
    if _IS_WINDOWS:
        return
    fd = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
