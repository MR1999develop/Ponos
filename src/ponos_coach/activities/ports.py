# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Porte della funzionalità attività: sorgenti di file, archivio degli originali, indice.

Le implementazioni reali stanno negli adattatori (`storage`); i fake in memoria e i test di
contratto in `tests/`. Nessuna porta registra dati di posizione o di salute.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Protocol

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityRecord,
    ArchivedFile,
    FileFormat,
)


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


# --- Archivio dei file originali ---------------------------------------------------------


class ArchiveError(Exception):
    """Radice degli errori dell'archivio dei file originali."""


class ArchiveWriteError(ArchiveError):
    """Scrittura non riuscita (disco pieno, permessi, I/O); nulla resta nell'archivio."""


class ArchiveFileMissingError(ArchiveError):
    """Il file indicato non c'è nell'archivio."""


class ArchiveTooLargeError(ArchiveError):
    """Il contenuto supera il massimo di byte ammesso; non è stato letto né scritto."""


class ArchiveIntegrityError(ArchiveError):
    """Il file c'è ma il suo SHA-256 o la sua dimensione non sono quelli registrati."""


class ActivityArchive(Protocol):
    """Archivio dei file originali (FIT, GPX), fonte di verità delle attività.

    Il nome del file lo genera il programma, mai un dato esterno. Una scrittura è durevole
    (file temporaneo, flush, rinomina) e idempotente: gli stessi byte, formato e anno danno
    lo stesso `ArchivedFile` senza duplicare nulla.
    """

    def store(self, content: bytes, file_format: FileFormat, start: datetime) -> ArchivedFile:
        """Salva i byte originali e ne restituisce percorso relativo, SHA-256 e dimensione.

        `start` (UTC) sceglie la sottocartella dell'anno. Alla fine il file è durevole e il
        chiamante può scrivere il record in indice.

        Raises:
            ArchiveTooLargeError: più di `MAX_ACTIVITY_FILE_BYTES` byte.
            ArchiveWriteError: scrittura non riuscita; l'archivio non cambia.
        """
        ...

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        """Contenuto del file, dopo aver verificato dimensione e SHA-256.

        Raises:
            ArchiveFileMissingError: il file non c'è.
            ArchiveTooLargeError: il file supera `max_bytes` (non viene letto oltre).
            ArchiveIntegrityError: dimensione o SHA-256 diversi da quelli registrati.
        """
        ...

    def verify(self, file: ArchivedFile) -> None:
        """Controlla dimensione e SHA-256 senza tenere il file in memoria.

        Raises:
            ArchiveFileMissingError: il file non c'è.
            ArchiveIntegrityError: dimensione o SHA-256 diversi da quelli registrati.
        """
        ...

    def list_orphan_temporaries(self) -> Sequence[PurePosixPath]:
        """Temporanei rimasti da una scrittura interrotta, relativi all'archivio, ordinati."""
        ...

    def remove_orphan_temporaries(self) -> int:
        """Cancella i temporanei orfani; restituisce quanti ne ha tolti. I file archiviati
        non si toccano mai."""
        ...


# --- Indice delle attività -----------------------------------------------------------------


class ActivityIndexError(Exception):
    """Radice degli errori dell'indice delle attività."""


class DuplicateActivityError(ActivityIndexError):
    """Esiste già un record con la stessa identità o con lo stesso id Garmin."""


class TransactionStateError(ActivityIndexError):
    """Transazione annidata: una transazione per volta, per connessione."""


class ActivityIndex(Protocol):
    """Indice delle attività di un profilo (derivato dai file, rigenerabile).

    Ogni operazione fuori da una transazione è atomica da sola. Dentro `transaction()` le
    modifiche (anche degli altri store dello stesso profilo) valgono tutte o nessuna.
    """

    def transaction(self) -> AbstractContextManager[None]:
        """Transazione: `with index.transaction(): ...`.

        All'uscita normale si conferma, con un'eccezione si annulla tutto e l'eccezione
        riparte. Non si annida (`TransactionStateError`) e non resta aperta durante rete o
        attese dell'utente.
        """
        ...

    def exists(self, identity: ActivityIdentity) -> bool:
        """Vero se c'è un record con questa identità."""
        ...

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        """Il record con questa identità, o None."""
        ...

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        """Record per cui `is_possible_duplicate(start, elapsed_s, ...)` è vero, per inizio.

        Serve a riconoscere la stessa attività arrivata in un altro formato. Non esclude
        l'identità uguale: chi chiama controlla prima `exists`.
        """
        ...

    def add(self, record: ActivityRecord) -> None:
        """Aggiunge un record. Il file in archivio deve già essere durevole.

        Raises:
            DuplicateActivityError: identità o id Garmin già presenti; nulla cambia.
        """
        ...

    def list_by_start(self) -> Sequence[ActivityRecord]:
        """Tutti i record per inizio crescente (a parità, per chiave di identità)."""
        ...
