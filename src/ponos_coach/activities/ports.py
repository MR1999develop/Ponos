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
from enum import Enum
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


# --- Import di file, cartelle e archivi ----------------------------------------------------


class Clock(Protocol):
    """Orologio iniettato: l'istante corrente, in UTC, per `imported_at`."""

    def now(self) -> datetime:
        """Istante corrente con fuso UTC."""
        ...


class ImportIssue(Enum):
    """Motivo per cui un file non diventa un'attività. Il valore è stabile.

    I primi otto coincidono (per valore) con `ActivityFileReason`; gli altri nascono prima della
    decodifica (la sorgente) o dopo (l'archivio del profilo).
    """

    EMPTY = "empty"
    TOO_LARGE = "too_large"
    CORRUPT = "corrupt"
    NOT_AN_ACTIVITY = "not_an_activity"
    UNSUPPORTED_FORMAT = "unsupported_format"
    EXTENSION_MISMATCH = "extension_mismatch"
    TOO_MANY_RECORDS = "too_many_records"
    TOO_LONG = "too_long"
    UNREADABLE = "unreadable"  # sparito, permessi, errore di I/O
    NOT_REGULAR_FILE = "not_regular_file"  # collegamento, giunzione, reparse point, FIFO...
    RESERVED_NAME = "reserved_name"  # nome riservato o non valido su Windows
    PATH_TOO_LONG = "path_too_long"
    UNSAFE_NAME = "unsafe_name"  # voce di uno ZIP con `..`, percorso assoluto, unità, controlli
    ARCHIVE_INVALID = "archive_invalid"  # ZIP corrotto, incoerente, con dimensione o CRC falsi
    ARCHIVE_LIMIT = "archive_limit"  # troppe voci, troppi byte, rapporto di compressione
    ENCRYPTED = "encrypted"  # voce cifrata
    NESTED_ARCHIVE = "nested_archive"  # ZIP dentro uno ZIP
    STORAGE_FAILED = "storage_failed"  # l'archivio del profilo non ha potuto scrivere


@dataclass(frozen=True, slots=True)
class ImportEntry:
    """Un file di attività candidato all'import, come lo elenca una sorgente.

    `entry_id` è opaco (lo assegna la sorgente), `name` è un nome mostrabile (percorso
    relativo; per la voce di uno ZIP `<zip>!<voce>`) senza caratteri di controllo, `size_bytes`
    la dimensione dichiarata. Nessun dato esterno diventa mai un percorso su disco.
    """

    entry_id: int
    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class SkippedEntry:
    """Un file o una voce che la sorgente ha saltato prima di leggerli, con il motivo."""

    name: str
    issue: ImportIssue


@dataclass(frozen=True, slots=True)
class ImportScan:
    """Esito dell'esame di una sorgente: cosa importare, cosa saltato, quante voci esaminate.

    Le due sequenze sono ordinate per nome (ordine stabile).
    """

    entries: tuple[ImportEntry, ...]
    skipped: tuple[SkippedEntry, ...]
    examined: int


class ImportLimit(Enum):
    """Tetto che ferma l'esame di una sorgente (nessun import parziale)."""

    ACTIVITY_FILES = "activity_files"
    EXAMINED_ENTRIES = "examined_entries"
    DEPTH = "depth"


class ImportSourceError(Exception):
    """Radice degli errori di una sorgente di import."""


class ImportLimitExceededError(ImportSourceError):
    """L'esame ha superato un tetto: non si importa nulla, serve una sorgente più piccola."""

    def __init__(self, limit: ImportLimit, maximum: int) -> None:
        super().__init__(f"{limit.value}>{maximum}")
        self.limit = limit
        self.maximum = maximum


class ImportSourceUnreadableError(ImportSourceError):
    """La cartella o l'elenco di file della sorgente non si può leggere."""


class ImportEntryError(ImportSourceError):
    """Un file non si è potuto leggere (sparito, troppo grande, ZIP incoerente...)."""

    def __init__(self, issue: ImportIssue) -> None:
        super().__init__(issue.value)
        self.issue = issue


class ImportSource(Protocol):
    """Sorgente dell'import: un elenco di file scelti o una cartella, ZIP compresi.

    `scan()` esamina senza importare (solo nomi e dimensioni, anche le directory degli ZIP) e
    si chiama una volta; `read()` restituisce i byte di un file già elencato, un ZIP già
    estratto in memoria e verificato. La sorgente possiede le sue risorse: chi la crea la
    chiude con `close()`. Non modifica mai i file d'origine.
    """

    def scan(self) -> ImportScan:
        """Esamina la sorgente.

        Raises:
            ImportLimitExceededError: oltre 50.000 file di attività, 500.000 voci esaminate o
                32 livelli di sottocartelle.
            ImportSourceUnreadableError: la cartella non si legge.
        """
        ...

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        """Byte del file, non oltre `max_bytes`.

        Raises:
            ImportEntryError: il file non si legge o è troppo grande (`issue` dice perché).
        """
        ...

    def close(self) -> None:
        """Rilascia le risorse; si può chiamare più volte."""
        ...
