# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Operazioni elementari di scrittura dell'archivio: la cucitura per l'iniezione di guasti.

`FileArchive` scrive solo attraverso `WriteOps`; i test la sostituiscono con una versione che
fallisce o "muore" all'N-esima operazione (T-DATI-02).

Durabilità. Su tutti i sistemi: temporaneo nella stessa cartella del file finale, `fsync` del
temporaneo prima della rinomina. POSIX: `os.replace` (atomica), poi `fsync` della cartella che
contiene il nome nuovo (e della cartella madre se la sottocartella dell'anno è nuova). Windows:
`MoveFileExW` con `MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH`, che torna a nome
durevole; una cartella non si può svuotare con `fsync`, quindi `sync_dir` non fa nulla.
"""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import Final, Protocol

_FILE_MODE: Final = 0o600
_DIR_MODE: Final = 0o700
_MOVEFILE_REPLACE_EXISTING: Final = 0x1
_MOVEFILE_WRITE_THROUGH: Final = 0x8
_CREATE_FLAGS: Final = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


class WriteOps(Protocol):
    """Operazioni con cui l'archivio scrive un file in modo durevole, nell'ordine d'uso."""

    def make_dir(self, path: Path) -> bool:
        """Crea la cartella (privata); vero se l'ha creata, falso se c'era già."""
        ...

    def create_exclusive(self, path: Path) -> int:
        """Crea un file nuovo in sola scrittura (fallisce se esiste); restituisce il descrittore."""
        ...

    def write(self, descriptor: int, data: memoryview) -> int:
        """Scrive al più `len(data)` byte; restituisce quanti ne ha scritti."""
        ...

    def fsync(self, descriptor: int) -> None:
        """Porta su disco i dati del file."""
        ...

    def close(self, descriptor: int) -> None:
        """Chiude il descrittore."""
        ...

    def replace(self, source: Path, destination: Path) -> None:
        """Rinomina `source` su `destination` (stesso volume), sostituendo l'esistente."""
        ...

    def sync_dir(self, path: Path) -> None:
        """Rende durevole il contenuto della cartella (i nomi), dove il sistema lo permette."""
        ...

    def unlink(self, path: Path) -> None:
        """Toglie il file (nessun errore se manca)."""
        ...


def _move_file_write_through(source: Path, destination: Path) -> None:
    if sys.platform != "win32":  # pragma: no cover - solo per il type checker
        raise NotImplementedError
    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    move.restype = ctypes.c_int
    flags = _MOVEFILE_REPLACE_EXISTING | _MOVEFILE_WRITE_THROUGH
    if not move(str(source), str(destination), flags):
        raise ctypes.WinError(ctypes.get_last_error())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class OsWriteOps:
    """Implementazione reale su `os` (e su `MoveFileExW` per la rinomina su Windows)."""

    def make_dir(self, path: Path) -> bool:
        """Vedi `WriteOps.make_dir`."""
        try:
            path.mkdir(mode=_DIR_MODE)
        except FileExistsError:
            if path.is_dir():
                return False
            raise
        return True

    def create_exclusive(self, path: Path) -> int:
        """Vedi `WriteOps.create_exclusive`: permessi 0600 su POSIX, mai link seguiti."""
        return os.open(path, _CREATE_FLAGS, _FILE_MODE)

    def write(self, descriptor: int, data: memoryview) -> int:
        """Vedi `WriteOps.write`."""
        return os.write(descriptor, data)

    def fsync(self, descriptor: int) -> None:
        """Vedi `WriteOps.fsync` (`FlushFileBuffers` su Windows)."""
        os.fsync(descriptor)

    def close(self, descriptor: int) -> None:
        """Vedi `WriteOps.close`."""
        os.close(descriptor)

    def replace(self, source: Path, destination: Path) -> None:
        """Vedi `WriteOps.replace`: con write-through su Windows."""
        if sys.platform == "win32":
            _move_file_write_through(source, destination)
        else:
            source.replace(destination)

    def sync_dir(self, path: Path) -> None:
        """Vedi `WriteOps.sync_dir`: `fsync` della cartella su POSIX, nulla su Windows."""
        if sys.platform != "win32":
            _fsync_directory(path)

    def unlink(self, path: Path) -> None:
        """Vedi `WriteOps.unlink`."""
        path.unlink(missing_ok=True)
