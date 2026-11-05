# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Porte della funzionalità attività: da dove arrivano i file FIT."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


class FitSourceError(Exception):
    """Radice degli errori di una sorgente di file FIT."""


class FitFileTooLargeError(FitSourceError):
    """Il file supera il massimo di byte richiesto; non è stato letto."""


class FitFileUnreadableError(FitSourceError):
    """Il file non si può leggere (sparito, permessi, errore di I/O)."""


@dataclass(frozen=True, slots=True)
class FitFileRef:
    """Riferimento opaco a un file di una sorgente.

    `name` è un nome mostrabile e unico nella sorgente; `size_bytes` la dimensione dichiarata
    dall'elenco (la lettura la ricontrolla).
    """

    name: str
    size_bytes: int


class FitFileSource(Protocol):
    """Sorgente di file FIT: un elenco e la lettura di un file con un massimo di byte."""

    def list_files(self) -> Sequence[FitFileRef]:
        """File disponibili, in ordine stabile (per nome)."""
        ...

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        """Contenuto del file.

        Raises:
            FitFileTooLargeError: se il file supera `max_bytes` (nulla viene letto oltre).
            FitFileUnreadableError: se il file non si può leggere.
        """
        ...
