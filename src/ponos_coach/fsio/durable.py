# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sostituzione durevole di un file: temporaneo esclusivo, flush, fsync, rinomina.

Windows: `MoveFileExW` con `MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`, che torna a
nome durevole; una cartella non si può svuotare con `fsync`, quindi `sync_directory` non fa
nulla. POSIX: `os.replace` (atomica), poi `fsync` della cartella che contiene il nome nuovo.
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
_REPLACE_ATTEMPTS: Final = 5  # antivirus e indicizzatori danno PermissionError transitori
_REPLACE_PAUSE_S: Final = 0.05
_IS_WINDOWS: Final[bool] = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo


def _move_file_write_through(source: Path, destination: Path) -> None:
    if sys.platform != "win32":  # pragma: no cover - solo per il type checker
        raise NotImplementedError
    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move.restype = ctypes.c_int
    flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
    if not move(str(source), str(destination), flags):
        raise ctypes.WinError(ctypes.get_last_error())


def replace_file(source: Path, destination: Path) -> None:
    """Rinomina `source` su `destination` (stesso volume), sostituendo l'esistente.

    Su Windows il nome nuovo è già durevole al ritorno; su POSIX serve `sync_directory`. Un
    `PermissionError` transitorio (Windows) si ritenta poche volte, poi si solleva.

    Raises:
        OSError: rinomina non riuscita; `destination` resta com'era.
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
    """Rende durevoli i nomi nella cartella (`fsync` su POSIX; su Windows non fa nulla)."""
    if _IS_WINDOWS:
        return
    descriptor = os.open(folder, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_file_atomically(path: Path, data: bytes) -> None:
    """Scrive `data` in `path` senza mai lasciarlo a metà.

    Temporaneo creato in modo esclusivo (0600) nella stessa cartella, flush, fsync, rinomina e
    sincronizzazione della cartella. Il file di prima resta intatto se qualcosa fallisce. La
    cartella deve esistere.

    Raises:
        OSError: scrittura o rinomina non riuscite; il temporaneo viene tolto.
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
    """Crea una sottocartella (un solo livello) di una cartella già privata.

    POSIX: modo 0700. Windows: nessun modo, così la cartella eredita il descrittore della
    cartella madre (`mkdir(mode=0o700)` di Python 3.13 per Windows ne scriverebbe un altro).
    Vero se l'ha creata, falso se c'era già una cartella.

    Raises:
        FileExistsError: esiste, ma non è una cartella.
        OSError: creazione non riuscita.
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
