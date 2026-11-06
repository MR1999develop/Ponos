# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Operazioni elementari di scrittura dell'archivio: la cucitura per l'iniezione di guasti.

`FileArchive` scrive solo attraverso `WriteOps`; i test la sostituiscono con una versione che
fallisce o "muore" all'N-esima operazione (T-DATI-02).

Durabilità. Su tutti i sistemi: temporaneo nella stessa cartella del file finale, `fsync` del
temporaneo prima della rinomina. Rinomina e sincronizzazione della cartella (anche della madre
se la sottocartella dell'anno è nuova) stanno in `ponos_coach.fsio`, condivise con l'elenco dei
profili; sulle cartelle nuove Windows eredita il descrittore privato della cartella del profilo.
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


class OsWriteOps:
    """Implementazione reale su `os` (e su `MoveFileExW` per la rinomina su Windows)."""

    def make_dir(self, path: Path) -> bool:
        """Vedi `WriteOps.make_dir`."""
        return make_subdirectory(path)

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
        replace_file(source, destination)

    def sync_dir(self, path: Path) -> None:
        """Vedi `WriteOps.sync_dir`: `fsync` della cartella su POSIX, nulla su Windows."""
        sync_directory(path)

    def unlink(self, path: Path) -> None:
        """Vedi `WriteOps.unlink`."""
        path.unlink(missing_ok=True)
