# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura con tetto di dimensione e scrittura atomica di piccoli file di configurazione.

La scrittura durevole sta in `ponos_coach.fsio`, condivisa con l'archivio."""

from __future__ import annotations

from pathlib import Path

from ponos_coach.fsio import write_file_atomically


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
    """Scrive `data` in `path` senza mai lasciarlo a metà (vedi `fsio.write_file_atomically`).

    Il file di prima resta intatto se qualcosa fallisce. La cartella deve esistere.

    Raises:
        OSError: scrittura o rinomina non riuscite; il temporaneo viene tolto.
    """
    write_file_atomically(path, data)
