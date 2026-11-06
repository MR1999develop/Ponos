# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caso d'uso "sincronizza con Garmin": elenco a pagine, giornale, download, archivio e indice.

Il guscio (worker, Composition Root) costruisce `SyncPorts` e chiama `synchronize_activities`.
Due fasi, in questo ordine, entrambe riprendibili dopo un'interruzione (PLAN.md 4.4):

1. **Elenco.** Completo la prima volta (`ListingKind.FULL`), poi incrementale (dalle più recenti
   finché una pagina intera è di id noti) o, su richiesta, "verifica completa". Ogni pagina
   aggiunge le sue voci al giornale e salva il cursore nella stessa transazione: dopo un kill
   l'elenco riparte dal cursore, senza perdere né duplicare voci. Il cursore si riprende anche se
   è di un'esecuzione precedente: le attività comparse nel frattempo in cima entrano alla
   sincronizzazione successiva, non si perdono.
2. **Voci da lavorare** (`PENDING` e `RETRYABLE`, dalla più recente). Prima l'abbinamento con
   un'attività già importata da file (nessun download); altrimenti download con pausa, estrazione
   dallo ZIP, decodifica e, in una sola transazione, archivio, record e stato della voce. Un kill
   in qualunque punto lascia la voce com'era e, al più, un file in archivio senza record, che la
   ripresa riscrive identico (l'archivio è idempotente).

Errori secondo come reagisce il chiamante: autenticazione richiesta, limite di frequenza oltre
i tetti, servizio irraggiungibile o errore dell'archivio **fermano** la sincronizzazione con un
motivo (`StopReason`); formato non supportato, file corrotto o troppo grande **falliscono la
voce** in modo definitivo e si va avanti. Nessun log di nomi o dati (solo il tipo di un errore
imprevisto), nessuna attesa che l'annullamento non possa interrompere.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Protocol, TypeVar

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    DEFAULT_POLICY,
    MAX_DOWNLOAD_BYTES,
    RETRYABLE_REASONS,
    Activity,
    ActivityOrigin,
    ActivityRecord,
    FailureReason,
    FileFormat,
    GarminActivityId,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RandomSource,
    SyncPolicy,
    SyncStatus,
    advance_cursor,
    estimate_remaining_s,
    find_matching_import,
    mark_downloaded,
    mark_failed,
    mark_matched,
    pause_between_requests_s,
    retry_wait_s,
    truncate_to_millisecond,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ActivitySource,
    ActivitySourceError,
    ArchiveError,
    ArchiveTooLargeError,
    AuthenticationRequiredError,
    Clock,
    DownloadTooLargeError,
    DuplicateActivityError,
    RateLimitedError,
    SyncJournal,
    TransientSourceError,
)

_LOG = logging.getLogger(__name__)

# Voci di fila fallite per servizio irraggiungibile (ciascuna dopo tutti i suoi tentativi) oltre
# le quali la sincronizzazione si ferma invece di consumare ritentativi su tutto lo storico.
MAX_CONSECUTIVE_UNREACHABLE: Final = 3
# Tetto alle voci elencate in una sola esecuzione: oltre, la risposta di Garmin è sospetta.
MAX_LISTED_ACTIVITIES: Final = 1_000_000

_T = TypeVar("_T")

_REASON_BY_FILE_ERROR: Final[Mapping[ActivityFileReason, FailureReason]] = {
    ActivityFileReason.EMPTY: FailureReason.CORRUPT_FILE,
    ActivityFileReason.TOO_LARGE: FailureReason.FILE_TOO_LARGE,
    ActivityFileReason.CORRUPT: FailureReason.CORRUPT_FILE,
    ActivityFileReason.NOT_AN_ACTIVITY: FailureReason.NOT_AN_ACTIVITY,
    ActivityFileReason.UNSUPPORTED_FORMAT: FailureReason.UNSUPPORTED_FORMAT,
    ActivityFileReason.EXTENSION_MISMATCH: FailureReason.CORRUPT_FILE,
    ActivityFileReason.TOO_MANY_RECORDS: FailureReason.FILE_TOO_LARGE,
    ActivityFileReason.TOO_LONG: FailureReason.CORRUPT_FILE,
}


# --- Dipendenze iniettate -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExtractedOriginal:
    """Il file di attività estratto dallo ZIP scaricato: i suoi byte e il suo formato."""

    content: bytes
    file_format: FileFormat


class OriginalExtractionError(Exception):
    """Lo ZIP scaricato non dà un file di attività; `reason` è sempre una causa definitiva
    (`ARCHIVE_INVALID` per uno ZIP non valido o ostile, `UNSUPPORTED_FORMAT` se contiene solo
    formati non supportati come il TCX, `FILE_TOO_LARGE`, `NOT_AN_ACTIVITY`...)."""

    def __init__(self, reason: FailureReason) -> None:
        if reason in RETRYABLE_REASONS or reason is FailureReason.ATTEMPTS_EXHAUSTED:
            raise ValueError("l'estrazione fallisce sempre per una causa definitiva")
        super().__init__(reason.value)
        self.reason = reason


OriginalExtractor = Callable[[bytes], ExtractedOriginal]
"""Dai byte dello ZIP scaricato al file di attività estratto, con i limiti dell'adattatore ZIP.

Solleva `OriginalExtractionError`; qualunque altra eccezione fa fallire la voce come
`ARCHIVE_INVALID`."""

ActivityDecoder = Callable[[bytes, FileFormat], Activity]
"""Decodifica un file di attività di formato noto (`decode_stored_activity`).

Solleva `ActivityFileError`; qualunque altra eccezione fa fallire la voce come `CORRUPT_FILE`."""

InterruptibleWait = Callable[[float], bool]
"""Attende al più `seconds` secondi e restituisce True se è stato chiesto l'annullamento prima
(`threading.Event.wait` ha proprio questa forma)."""


class SyncClock(Clock, Protocol):
    """Orologio della sincronizzazione: `now()` per gli istanti (UTC), `monotonic()` per le
    durate (secondi, mai all'indietro)."""

    def monotonic(self) -> float:
        """Secondi di un orologio monotono."""
        ...


@dataclass(frozen=True, slots=True)
class SyncPorts:
    """Tutto ciò che la sincronizzazione riceve dal Composition Root.

    `index`, `journal` e `archive` sono quelli del profilo aperto dal worker (stessa connessione
    e stessa transazione per indice e giornale). `rng` dà il jitter.
    """

    source: ActivitySource
    index: ActivityIndex
    journal: SyncJournal
    archive: ActivityArchive
    extract_original: OriginalExtractor
    decode: ActivityDecoder
    clock: SyncClock
    wait: InterruptibleWait
    rng: RandomSource
    policy: SyncPolicy = DEFAULT_POLICY


# --- Richiesta ed esito -------------------------------------------------------------------------


class SyncMode(Enum):
    """Che cosa si chiede."""

    SYNC = "sync"  # completo la prima volta (o finché non è finito), poi incrementale
    VERIFY = "verify"  # "Verifica completa": rielenca tutto lo storico senza riscaricare


class SyncPhase(Enum):
    """In quale fase si trova la sincronizzazione."""

    LISTING = "listing"
    DOWNLOADING = "downloading"


@dataclass(frozen=True, slots=True)
class SyncProgress:
    """Avanzamento: `done` su `total` (None se non si sa) della fase, e il tempo stimato per
    finirla (None finché mancano le misure)."""

    phase: SyncPhase
    done: int
    total: int | None
    remaining_s: float | None = None


class StopReason(Enum):
    """Perché la sincronizzazione non è arrivata in fondo. Il valore è stabile."""

    CANCELLED = "cancelled"
    AUTHENTICATION_REQUIRED = "authentication_required"  # serve un nuovo login
    RATE_LIMITED = "rate_limited"  # Garmin limita la frequenza oltre i tetti
    SERVICE_UNAVAILABLE = "service_unavailable"  # Garmin non risponde
    REMOTE_ERROR = "remote_error"  # l'elenco è stato rifiutato o non è interpretabile
    STORAGE_ERROR = "storage_error"  # l'archivio del profilo non scrive (disco pieno...)
    LISTING_LIMIT = "listing_limit"  # più di `MAX_LISTED_ACTIVITIES` voci


@dataclass(frozen=True, slots=True)
class EntryFailure:
    """Una voce fallita in questa esecuzione e la sua causa (ritentabile se la causa è in
    `RETRYABLE_REASONS`)."""

    garmin_id: GarminActivityId
    reason: FailureReason


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """Esito di una sincronizzazione.

    `stop` è None se è arrivata in fondo (solo allora si è registrata la fine in
    `last_completed_at`). `new_entries` sono le voci aggiunte al giornale, `downloaded` e
    `matched` quelle portate a termine in questa esecuzione, `failures` le voci fallite (anche se
    ritentabili); `status_counts` il giornale intero a fine esecuzione.
    """

    kind: ListingKind
    stop: StopReason | None
    new_entries: int
    downloaded: int
    matched: int
    failures: tuple[EntryFailure, ...]
    status_counts: Mapping[SyncStatus, int]

    @property
    def completed(self) -> bool:
        """Vero se la sincronizzazione è arrivata in fondo."""
        return self.stop is None

    @property
    def cancelled(self) -> bool:
        """Vero se l'ha interrotta chi l'ha avviata."""
        return self.stop is StopReason.CANCELLED

    def failures_by_reason(self) -> Mapping[FailureReason, int]:
        """Quante voci sono fallite per ciascuna causa (solo le cause presenti)."""
        counts: dict[FailureReason, int] = {}
        for failure in self.failures:
            counts[failure.reason] = counts.get(failure.reason, 0) + 1
        return MappingProxyType(counts)


def _ignore_progress(_progress: SyncProgress) -> None:
    """Avanzamento non richiesto."""


def _never_cancelled() -> bool:
    return False


def listing_kind_for(mode: SyncMode, full_listing: ListingCursor | None) -> ListingKind:
    """Quale elenco percorrere: la verifica, il completo finché non è finito, poi l'incrementale."""
    if mode is SyncMode.VERIFY:
        return ListingKind.VERIFY
    if full_listing is None or not full_listing.completed:
        return ListingKind.FULL
    return ListingKind.INCREMENTAL


def synchronize_activities(
    ports: SyncPorts,
    mode: SyncMode = SyncMode.SYNC,
    *,
    report: Callable[[SyncProgress], None] = _ignore_progress,
    is_cancelled: Callable[[], bool] = _never_cancelled,
) -> SyncOutcome:
    """Sincronizza il profilo con Garmin.

    Prima l'elenco (completo, incrementale o verifica, vedi `listing_kind_for`), poi le voci da
    lavorare dalla più recente. `report` riceve l'avanzamento di ogni fase;
    `is_cancelled()` si controlla prima di ogni richiesta, voce e pagina, e `ports.wait`
    interrompe le pause: l'annullamento ferma entro una chiamata e lascia giornale e indice
    coerenti. Un'esecuzione interrotta o fermata si riprende rilanciando.

    Non solleva per i fallimenti attesi (vedi `StopReason` e `EntryFailure`): sono nell'esito.
    Gli errori di programmazione e i guasti del database si propagano; la transazione in corso
    si annulla.
    """
    kind = listing_kind_for(mode, ports.journal.load_cursor(ListingKind.FULL))
    return _Run(ports, kind, report, is_cancelled).execute()


# --- Esecuzione ---------------------------------------------------------------------------------


class _StopError(Exception):
    """Fine anticipata di un'esecuzione (controllo di flusso interno)."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _RequestFailedError(Exception):
    """Una richiesta ha esaurito i tentativi (o l'attesa che Garmin chiede è oltre il tetto)."""

    def __init__(self, reason: FailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _Run:
    """Stato di una sola esecuzione: contatori, durate misurate, ultima richiesta."""

    def __init__(
        self,
        ports: SyncPorts,
        kind: ListingKind,
        report: Callable[[SyncProgress], None],
        is_cancelled: Callable[[], bool],
    ) -> None:
        self._p = ports
        self._kind = kind
        self._report = report
        self._is_cancelled = is_cancelled
        self._new_entries = 0
        self._downloaded = 0
        self._matched = 0
        self._failures: list[EntryFailure] = []
        self._durations_s: list[float] = []
        self._requested_before = False
        self._unreachable_in_a_row = 0

    def execute(self) -> SyncOutcome:
        stop: StopReason | None = None
        try:
            self._list()
            self._work_through_entries()
        except _StopError as stopped:
            stop = stopped.reason
        if stop is None:
            self._p.journal.record_completed(self._now())
        return SyncOutcome(
            self._kind,
            stop,
            self._new_entries,
            self._downloaded,
            self._matched,
            tuple(self._failures),
            MappingProxyType(dict(self._p.journal.counts())),
        )

    # --- Elenco ---------------------------------------------------------------------------------

    def _list(self) -> None:
        journal = self._p.journal
        limit = self._p.policy.page_size
        cursor = journal.load_cursor(self._kind)
        if cursor is None or cursor.completed:
            cursor = ListingCursor.start()
        while not cursor.completed:
            self._check_cancelled()
            if cursor.next_offset >= MAX_LISTED_ACTIVITIES:
                raise _StopError(StopReason.LISTING_LIMIT)
            self._report(SyncProgress(SyncPhase.LISTING, cursor.next_offset, cursor.declared_total))
            cursor = self._list_one_page(cursor, limit)
        self._report(SyncProgress(SyncPhase.LISTING, cursor.next_offset, cursor.next_offset))

    def _list_one_page(self, cursor: ListingCursor, limit: int) -> ListingCursor:
        offset = cursor.next_offset
        try:
            page = self._request(lambda: self._p.source.list_page(offset, limit))
        except _RequestFailedError as failed:
            raise _StopError(_stop_for(failed.reason)) from failed
        except ActivitySourceError as error:  # elenco rifiutato o non interpretabile
            raise _StopError(StopReason.REMOTE_ERROR) from error
        known: frozenset[GarminActivityId] | None = None
        if self._kind is ListingKind.INCREMENTAL:
            known = self._p.journal.known_ids([item.garmin_id for item in page.items])
        try:
            following = advance_cursor(cursor, page, limit, known)
        except ValueError as error:  # pagina fuori dal contratto della porta
            raise _StopError(StopReason.REMOTE_ERROR) from error
        with self._p.index.transaction():
            added = self._p.journal.add_pending(page.items, self._now())
            self._p.journal.save_cursor(self._kind, following)
        self._new_entries += added
        return following

    # --- Voci da lavorare -----------------------------------------------------------------------

    def _work_through_entries(self) -> None:
        counts = self._p.journal.counts()
        total = counts[SyncStatus.PENDING] + counts[SyncStatus.RETRYABLE]
        entries = self._p.journal.next_to_process(total) if total else ()
        for done, entry in enumerate(entries):
            self._check_cancelled()
            remaining = estimate_remaining_s(self._durations_s, total - done)
            self._report(SyncProgress(SyncPhase.DOWNLOADING, done, total, remaining))
            started = self._p.clock.monotonic()
            self._work_on(entry)
            self._durations_s.append(max(self._p.clock.monotonic() - started, 0.0))
        self._report(SyncProgress(SyncPhase.DOWNLOADING, total, total, 0.0))

    def _work_on(self, entry: JournalEntry) -> None:
        if self._match_with_import(entry):
            return
        zipped = self._download(entry)
        if zipped is None:
            return
        extracted = self._extract(entry, zipped)
        if extracted is None:
            return
        activity = self._decode(entry, extracted)
        if activity is None:
            return
        self._save(entry, extracted, activity)

    def _match_with_import(self, entry: JournalEntry) -> bool:
        """Abbina la voce a un'attività già importata da file: nessun download. Vero se fatto."""
        remote = entry.remote
        candidates = self._p.index.find_possible_duplicates(remote.start, remote.duration_s)
        match = find_matching_import(remote, candidates)
        if match is None:
            return False
        try:
            with self._p.index.transaction():
                self._p.index.link_garmin_id(match.identity, entry.garmin_id)
                self._p.journal.update(mark_matched(entry, self._now()))
        except DuplicateActivityError:  # l'id è già di un altro record: si scarica e si vede
            return False
        self._matched += 1
        return True

    def _download(self, entry: JournalEntry) -> bytes | None:
        garmin_id = entry.garmin_id
        try:
            content = self._request(
                lambda: self._p.source.download_original(garmin_id, MAX_DOWNLOAD_BYTES)
            )
        except _RequestFailedError as failed:
            self._fail(entry, failed.reason)
            self._stop_if_unreachable(failed.reason)
            return None
        except DownloadTooLargeError:
            self._fail(entry, FailureReason.FILE_TOO_LARGE)
        except ActivitySourceError:  # originale assente, rifiutato o non interpretabile
            self._fail(entry, FailureReason.REMOTE_REJECTED)
        else:
            self._unreachable_in_a_row = 0
            return content
        self._unreachable_in_a_row = 0
        return None

    def _stop_if_unreachable(self, reason: FailureReason) -> None:
        """Dopo una voce fallita per rete o limite: se Garmin non risponde più, ci si ferma."""
        if reason is FailureReason.RATE_LIMITED:
            raise _StopError(StopReason.RATE_LIMITED)
        self._unreachable_in_a_row += 1
        if self._unreachable_in_a_row >= MAX_CONSECUTIVE_UNREACHABLE:
            raise _StopError(StopReason.SERVICE_UNAVAILABLE)

    def _extract(self, entry: JournalEntry, zipped: bytes) -> ExtractedOriginal | None:
        try:
            return self._p.extract_original(zipped)
        except OriginalExtractionError as error:
            self._fail(entry, error.reason)
        except Exception as error:  # un estrattore che non dovrebbe sollevare altro
            _log_unexpected("estrazione", error)
            self._fail(entry, FailureReason.ARCHIVE_INVALID)
        return None

    def _decode(self, entry: JournalEntry, extracted: ExtractedOriginal) -> Activity | None:
        try:
            return self._p.decode(extracted.content, extracted.file_format)
        except ActivityFileError as error:
            self._fail(entry, _REASON_BY_FILE_ERROR[error.reason])
        except Exception as error:  # un lettore che non dovrebbe sollevare altro
            _log_unexpected("decodifica", error)
            self._fail(entry, FailureReason.CORRUPT_FILE)
        return None

    def _save(self, entry: JournalEntry, extracted: ExtractedOriginal, activity: Activity) -> None:
        """Archivio, record e stato della voce in una sola transazione."""
        try:
            with self._p.index.transaction():
                status = self._record_activity(entry, extracted, activity)
        except DuplicateActivityError:
            # Il file, o l'id Garmin, è già di un altro record: l'attività c'è già nel profilo.
            self._p.journal.update(mark_downloaded(entry, self._now()))
            status = SyncStatus.DOWNLOADED
        except ArchiveTooLargeError:
            self._fail(entry, FailureReason.FILE_TOO_LARGE)
            return
        except ArchiveError as error:  # non è colpa della voce: ci si ferma, la voce resta com'era
            raise _StopError(StopReason.STORAGE_ERROR) from error
        if status is SyncStatus.MATCHED:
            self._matched += 1
        else:
            self._downloaded += 1

    def _record_activity(
        self, entry: JournalEntry, extracted: ExtractedOriginal, activity: Activity
    ) -> SyncStatus:
        index = self._p.index
        garmin_id = entry.garmin_id
        now = self._now()
        existing = index.get(activity.identity)
        if existing is not None:  # stesso file già importato: si collega l'id
            if existing.garmin_activity_id is None:
                index.link_garmin_id(existing.identity, garmin_id)
            self._p.journal.update(mark_downloaded(entry, now))
            return SyncStatus.DOWNLOADED
        twin = next(
            (
                record
                for record in index.find_possible_duplicates(activity.start, activity.elapsed_s)
                if record.garmin_activity_id is None
                and record.file_format is not activity.file_format
            ),
            None,
        )
        if twin is not None:  # la stessa attività importata in un altro formato
            index.link_garmin_id(twin.identity, garmin_id)
            self._p.journal.update(mark_matched(entry, now))
            return SyncStatus.MATCHED
        archived = self._p.archive.store(extracted.content, activity.file_format, activity.start)
        index.add(
            ActivityRecord.from_activity(
                activity, archived, ActivityOrigin.GARMIN, now, garmin_id.value
            )
        )
        self._p.journal.update(mark_downloaded(entry, now))
        return SyncStatus.DOWNLOADED

    def _fail(self, entry: JournalEntry, reason: FailureReason) -> None:
        failed = mark_failed(entry, reason, self._now(), self._p.policy.max_syncs_per_activity)
        self._p.journal.update(failed)
        if failed.failure is not None:
            self._failures.append(EntryFailure(entry.garmin_id, failed.failure))

    # --- Richieste: pausa, backoff, annullamento ------------------------------------------------

    def _request(self, call: Callable[[], _T]) -> _T:
        """Una richiesta a Garmin con pausa prima e ritentativi sugli errori transitori.

        Raises:
            _RequestFailedError: tentativi esauriti, o attesa suggerita oltre il tetto del backoff.
            _StopError: autenticazione richiesta o annullamento.
        Gli errori definitivi della sorgente passano al chiamante.
        """
        self._check_cancelled()
        if self._requested_before:
            self._sleep(pause_between_requests_s(self._p.policy, self._p.rng))
        self._requested_before = True
        failures = 0
        while True:
            self._check_cancelled()
            try:
                return call()
            except AuthenticationRequiredError as error:
                raise _StopError(StopReason.AUTHENTICATION_REQUIRED) from error
            except RateLimitedError as error:
                failures += 1
                self._back_off(failures, FailureReason.RATE_LIMITED, error.retry_after_s)
            except TransientSourceError:
                failures += 1
                self._back_off(failures, FailureReason.TRANSIENT_ERROR, None)

    def _back_off(self, failures: int, reason: FailureReason, suggested_s: float | None) -> None:
        wait_s = retry_wait_s(self._p.policy, failures, self._p.rng, suggested_s)
        if wait_s is None:
            raise _RequestFailedError(reason)
        self._sleep(wait_s)

    def _sleep(self, seconds: float) -> None:
        if self._p.wait(seconds):
            raise _StopError(StopReason.CANCELLED)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise _StopError(StopReason.CANCELLED)

    def _now(self) -> datetime:
        return truncate_to_millisecond(self._p.clock.now())


def _stop_for(reason: FailureReason) -> StopReason:
    return (
        StopReason.RATE_LIMITED
        if reason is FailureReason.RATE_LIMITED
        else StopReason.SERVICE_UNAVAILABLE
    )


def _log_unexpected(step: str, error: Exception) -> None:
    """Un solo record per errore imprevisto, col solo tipo: il messaggio può contenere dati."""
    _LOG.error("sincronizzazione: errore imprevisto in %s (%s)", step, type(error).__name__)
