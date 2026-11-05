# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sorgente di file FIT da una cartella (solo il primo livello).

Adattatore minimo di `FitFileSource`. L'import ricorsivo con i tetti di file, voci e livelli è
della fase 1.
"""

from __future__ import annotations

import os
from pathlib import Path

from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileTooLargeError,
    FitFileUnreadableError,
)

_SUFFIX = ".fit"


class FitFolder:
    """File `*.fit` regolari direttamente dentro una cartella; i collegamenti si ignorano."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def list_files(self) -> list[FitFileRef]:
        """File FIT del primo livello, ordinati per nome. Cartella illeggibile → errore."""
        try:
            with os.scandir(self._folder) as entries:
                found = [
                    FitFileRef(entry.name, entry.stat(follow_symlinks=False).st_size)
                    for entry in entries
                    if entry.name.lower().endswith(_SUFFIX) and entry.is_file(follow_symlinks=False)
                ]
        except OSError as exc:
            raise FitFileUnreadableError(str(self._folder.name)) from exc
        return sorted(found, key=lambda ref: ref.name)

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        """Legge il file se non supera `max_bytes`; la dimensione si controlla sull'handle."""
        if Path(ref.name).name != ref.name or not ref.name.lower().endswith(_SUFFIX):
            raise FitFileUnreadableError(ref.name)
        try:
            with (self._folder / ref.name).open("rb") as handle:
                if os.fstat(handle.fileno()).st_size > max_bytes:
                    raise FitFileTooLargeError(ref.name)
                data = handle.read(max_bytes + 1)
        except OSError as exc:
            raise FitFileUnreadableError(ref.name) from exc
        if len(data) > max_bytes:
            raise FitFileTooLargeError(ref.name)
        return data
