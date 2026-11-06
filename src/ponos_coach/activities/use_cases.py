# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caso d'uso "importa attività": file scelti, cartelle e archivi ZIP nel profilo aperto.

Il guscio (interfaccia, worker) crea la sorgente e la chiude; qui, per ogni file elencato, si
legge, si decodifica, si controlla che l'attività non ci sia già e si salva: il file originale
nell'archivio prima, il record nell'indice poi, nella stessa transazione. L'import è
idempotente (stessi file, stesso profilo: tutti "già presenti") e annullabile tra un file e
l'altro. Nessun log: nomi e posizioni sono dati personali.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.decoding import ActivityFileError, decode_activity_file
from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityOrigin,
    ActivityRecord,
    ArchivedFile,
    truncate_to_millisecond,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ArchiveError,
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
    Clock,
    DuplicateActivityError,
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportScanCancelledError,
    ImportSource,
)

_LOG = logging.getLogger(__name__)


class ImportStatus(Enum):
    """Esito dell'import di un file."""

    IMPORTED = "imported"
    ALREADY_PRESENT = "already_present"  # stessa identità già nell'indice
    REPAIRED = "repaired"  # già nell'indice, ma il suo file in archivio mancava o era alterato
    DUPLICATE_OF_OTHER_FORMAT = "duplicate_of_other_format"  # stessa attività, altro formato
    IGNORED = "ignored"  # il file non è un'attività (per esempio un FIT di impostazioni)
    FAILED = "failed"  # illeggibile, rifiutato o non salvato: `issue` dice perché


@dataclass(frozen=True, slots=True)
class FileImportResult:
    """Esito per file. `issue` c'è se e solo se l'esito è IGNORED o FAILED; `matches` è
    l'identità dell'attività già presente per ALREADY_PRESENT, REPAIRED e
    DUPLICATE_OF_OTHER_FORMAT."""

    name: str
    status: ImportStatus
    issue: ImportIssue | None = None
    matches: ActivityIdentity | None = None

    def __post_init__(self) -> None:
        has_issue = self.status in (ImportStatus.IGNORED, ImportStatus.FAILED)
        has_match = self.status in (
            ImportStatus.ALREADY_PRESENT,
            ImportStatus.REPAIRED,
            ImportStatus.DUPLICATE_OF_OTHER_FORMAT,
        )
        if has_issue != (self.issue is not None) or has_match != (self.matches is not None):
            raise ValueError("motivo e attività corrispondente non coerenti con l'esito")


@dataclass(frozen=True, slots=True)
class ImportSummary:
    """Riepilogo finale: un esito per file (prima i saltati dalla sorgente, poi gli altri, in
    ordine di nome). Con `cancelled` mancano i file non ancora trattati."""

    results: tuple[FileImportResult, ...]
    cancelled: bool

    def count(self, status: ImportStatus) -> int:
        """Quanti file hanno questo esito."""
        return sum(1 for result in self.results if result.status is status)

    def named(self, status: ImportStatus) -> tuple[FileImportResult, ...]:
        """Gli esiti con questo stato, nello stesso ordine del riepilogo."""
        return tuple(result for result in self.results if result.status is status)


def _ignore_progress(_done: int, _total: int) -> None:
    """Avanzamento non richiesto."""


def _never_cancelled() -> bool:
    return False


def _other_format_twin(index: ActivityIndex, activity: Activity) -> ActivityIdentity | None:
    """Identità di un'attività già presente, di un altro formato, con stesso inizio e durata."""
    for record in index.find_possible_duplicates(activity.start, activity.elapsed_s):
        if record.file_format is not activity.file_format:
            return record.identity
    return None


def _is_the_archived_content(archived: ArchivedFile, data: bytes) -> bool:
    return (
        len(data) == archived.size_bytes and hashlib.sha256(data).hexdigest() == archived.sha256_hex
    )


def _check_archived_file(
    record: ActivityRecord, data: bytes, name: str, archive: ActivityArchive
) -> FileImportResult:
    """Attività già nell'indice: il suo file in archivio deve esserci ed essere integro.

    Se manca o è alterato e il file importato ha proprio i byte registrati (stesso SHA-256 e
    stessa dimensione), lo si rimette in archivio: esito `REPAIRED`. Se i byte sono altri (la
    stessa identità, un contenuto diverso) non si ripara nulla: `ALREADY_PRESENT`.
    """
    try:
        archive.verify(record.archived)
    except (ArchiveFileMissingError, ArchiveIntegrityError):
        if _is_the_archived_content(record.archived, data):
            archive.store(data, record.file_format, record.start)
            return FileImportResult(name, ImportStatus.REPAIRED, matches=record.identity)
    return FileImportResult(name, ImportStatus.ALREADY_PRESENT, matches=record.identity)


def _add_if_new(
    activity: Activity,
    data: bytes,
    name: str,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    existing = index.get(activity.identity)
    if existing is not None:
        return _check_archived_file(existing, data, name, archive)
    twin = _other_format_twin(index, activity)
    if twin is not None:
        return FileImportResult(name, ImportStatus.DUPLICATE_OF_OTHER_FORMAT, matches=twin)
    archived = archive.store(data, activity.file_format, activity.start)
    record = ActivityRecord.from_activity(
        activity,
        archived,
        ActivityOrigin.IMPORT,
        truncate_to_millisecond(clock.now()),
    )
    index.add(record)
    return FileImportResult(name, ImportStatus.IMPORTED)


def _store(
    activity: Activity,
    data: bytes,
    name: str,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    """Controlla i duplicati, archivia il file e scrive il record in un'unica transazione."""
    try:
        with index.transaction():
            return _add_if_new(activity, data, name, archive, index, clock)
    except DuplicateActivityError:
        return FileImportResult(name, ImportStatus.ALREADY_PRESENT, matches=activity.identity)
    except ArchiveTooLargeError:
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.TOO_LARGE)
    except ArchiveError:
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)
    except Exception as error:  # guasto imprevisto: questo file fallisce, gli altri vanno avanti
        _log_unexpected(error)
        return FileImportResult(name, ImportStatus.FAILED, ImportIssue.STORAGE_FAILED)


def _log_unexpected(error: Exception) -> None:
    """Un solo record per errore imprevisto, col solo tipo: il messaggio può contenere nomi,
    percorsi o dati personali."""
    _LOG.error("import: errore imprevisto su un file (%s)", type(error).__name__)


def _import_entry(
    entry: ImportEntry,
    source: ImportSource,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
) -> FileImportResult:
    try:
        data = source.read(entry, MAX_ACTIVITY_FILE_BYTES)
    except ImportEntryError as error:
        return FileImportResult(entry.name, ImportStatus.FAILED, error.issue)
    except Exception as error:  # guasto imprevisto: questo file fallisce, gli altri vanno avanti
        _log_unexpected(error)
        return FileImportResult(entry.name, ImportStatus.FAILED, ImportIssue.UNREADABLE)
    try:
        activity = decode_activity_file(data, entry.name)
    except ActivityFileError as error:
        issue = ImportIssue(error.reason.value)
        status = (
            ImportStatus.IGNORED if issue is ImportIssue.NOT_AN_ACTIVITY else ImportStatus.FAILED
        )
        return FileImportResult(entry.name, status, issue)
    except Exception as error:  # un lettore che non dovrebbe sollevare altro: il file è rifiutato
        _log_unexpected(error)
        return FileImportResult(entry.name, ImportStatus.FAILED, ImportIssue.CORRUPT)
    return _store(activity, data, entry.name, archive, index, clock)


def import_activities(
    source: ImportSource,
    archive: ActivityArchive,
    index: ActivityIndex,
    clock: Clock,
    *,
    report: Callable[[int, int], None] = _ignore_progress,
    is_cancelled: Callable[[], bool] = _never_cancelled,
) -> ImportSummary:
    """Importa nel profilo i file di attività della sorgente.

    Prima esamina la sorgente (`source.scan()`): se supera un tetto non si importa nulla. Poi,
    per ogni file in ordine di nome, salva il file originale nell'archivio e il record
    nell'indice in una transazione; un'attività già presente (stessa identità) o già arrivata
    in un altro formato (stesso inizio e durata) non si salva due volte. Un file con errore non
    ferma gli altri. `report(fatti, totale)` si chiama prima del primo file e dopo ciascuno;
    `is_cancelled()` si controlla prima di ogni file e, se vero, il riepilogo è parziale
    (`cancelled`) e rilanciare l'import riprende senza duplicati.

    Anche l'esame della sorgente si può annullare: il riepilogo è allora vuoto e `cancelled`.
    Un errore imprevisto su un singolo file diventa l'esito FAILED di quel file (registrato una
    volta, col solo tipo dell'eccezione) e l'import continua. Un file già presente il cui
    originale in archivio manca o è alterato si ripara (esito REPAIRED) se il file importato ha i
    byte registrati.

    Raises:
        ImportLimitExceededError: la sorgente supera un tetto (file di attività, voci esaminate
            o livelli): nulla è stato importato.
        ImportSourceUnreadableError: la sorgente non si può leggere.
    """
    try:
        scan = source.scan(is_cancelled)
    except ImportScanCancelledError:
        return ImportSummary((), cancelled=True)
    results = [
        FileImportResult(item.name, ImportStatus.FAILED, item.issue) for item in scan.skipped
    ]
    total = len(scan.entries)
    report(0, total)
    for done, entry in enumerate(scan.entries):
        if is_cancelled():
            return ImportSummary(tuple(results), cancelled=True)
        results.append(_import_entry(entry, source, archive, index, clock))
        report(done + 1, total)
    return ImportSummary(tuple(results), cancelled=False)
