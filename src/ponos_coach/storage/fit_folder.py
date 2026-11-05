# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgente di file FIT da una cartella (solo il primo livello).

Adattatore minimo di `FitFileSource`. L'import ricorsivo con i tetti di file, voci e livelli è
della fase 1.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import BinaryIO

from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileTooLargeError,
    FitFileUnreadableError,
)

_SUFFIX = ".fit"
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT: collegamenti e giunzioni su Windows

# Apertura sicura: sola lettura, niente collegamenti simbolici dove il sistema lo sa evitare,
# non bloccante su POSIX (un FIFO chiamato `x.fit` non deve bloccare per sempre), binaria su
# Windows, non ereditata dai processi figli.
_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def _listed(entry: os.DirEntry[str]) -> FitFileRef | None:
    """Riferimento di una voce `*.fit` regolare; una voce che non si legge ha dimensione 0.

    Una voce sparita o illeggibile durante l'elenco non fa fallire tutto: resta nell'elenco e
    la lettura la rifiuta come illeggibile.
    """
    if not entry.name.lower().endswith(_SUFFIX):
        return None
    try:
        if not entry.is_file(follow_symlinks=False):
            return None
        return FitFileRef(entry.name, entry.stat(follow_symlinks=False).st_size)
    except OSError:
        return FitFileRef(entry.name, 0)


def _is_link(path: Path) -> bool:
    status = os.lstat(path)
    attributes = getattr(status, "st_file_attributes", 0)
    return stat.S_ISLNK(status.st_mode) or bool(attributes & _REPARSE_POINT)


class FitFolder:
    """File `*.fit` regolari direttamente dentro una cartella; i collegamenti si ignorano."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def list_files(self) -> list[FitFileRef]:
        """File FIT del primo livello, ordinati per nome. Cartella illeggibile → errore."""
        try:
            with os.scandir(self._folder) as entries:
                found = [ref for entry in entries if (ref := _listed(entry)) is not None]
        except OSError as exc:
            raise FitFileUnreadableError(str(self._folder.name)) from exc
        return sorted(found, key=lambda ref: ref.name)

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        """Legge il file se è regolare e non supera `max_bytes`.

        Tipo e dimensione si controllano sull'handle aperto, non sul nome: un collegamento, un
        FIFO o un dispositivo sono illeggibili, e un file troppo grande non si legge.
        """
        if Path(ref.name).name != ref.name or not ref.name.lower().endswith(_SUFFIX):
            raise FitFileUnreadableError(ref.name)
        path = self._folder / ref.name
        try:
            if _is_link(path):
                raise FitFileUnreadableError(ref.name)
            descriptor = os.open(path, _OPEN_FLAGS)
        except OSError as exc:
            raise FitFileUnreadableError(ref.name) from exc
        try:
            with os.fdopen(descriptor, "rb") as handle:
                return self._read_regular(handle, ref.name, max_bytes)
        except OSError as exc:
            raise FitFileUnreadableError(ref.name) from exc

    @staticmethod
    def _read_regular(handle: BinaryIO, name: str, max_bytes: int) -> bytes:
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise FitFileUnreadableError(name)
        if status.st_size > max_bytes:
            raise FitFileTooLargeError(name)
        data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FitFileTooLargeError(name)
        return data
