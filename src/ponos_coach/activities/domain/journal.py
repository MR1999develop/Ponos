# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Giornale della sincronizzazione: stato di ogni attività remota, cursore dell'elenco.

Il giornale rende la sincronizzazione riprendibile (PLAN.md 4.4): ogni id remoto è una voce con
uno stato. Le transizioni ammesse sono funzioni pure; non esiste uno stato "in corso", perché
download, archivio e conferma stanno in una sola transazione: un'interruzione lascia la voce
com'era.
"""

from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from ponos_coach.activities.domain.garmin_id import GarminActivityId
from ponos_coach.activities.domain.remote import MAX_PAGE_SIZE, ActivityPage, RemoteActivity
from ponos_coach.activities.domain.timing import check_utc


class SyncStatus(Enum):
    """Stato di una voce del giornale. Il valore è stabile (dati salvati)."""

    PENDING = "pending"  # da scaricare
    DOWNLOADED = "downloaded"  # scaricata, archiviata e indicizzata
    MATCHED = "matched"  # abbinata a un'attività già importata da file: nessun download
    RETRYABLE = "retryable"  # fallita per una causa transitoria: si ritenta alla prossima volta
    FAILED = "failed"  # fallita in modo definitivo: non si ritenta


class FailureReason(Enum):
    """Perché una voce è fallita. Il valore è stabile (dati salvati)."""

    # Ritentabili: la causa può passare.
    TRANSIENT_ERROR = "transient_error"
    RATE_LIMITED = "rate_limited"
    # Definitive: ritentare darebbe lo stesso esito.
    UNSUPPORTED_FORMAT = "unsupported_format"  # per esempio un TCX
    CORRUPT_FILE = "corrupt_file"
    FILE_TOO_LARGE = "file_too_large"
    NOT_AN_ACTIVITY = "not_an_activity"
    ARCHIVE_INVALID = "archive_invalid"  # lo ZIP scaricato non è valido o è ostile
    REMOTE_REJECTED = "remote_rejected"  # Garmin dice che l'originale non c'è o non si dà
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"  # troppe sincronizzazioni fallite


RETRYABLE_REASONS: Final = frozenset({FailureReason.TRANSIENT_ERROR, FailureReason.RATE_LIMITED})

WORKABLE_STATUSES: Final = frozenset({SyncStatus.PENDING, SyncStatus.RETRYABLE})
_FAILURE_STATUSES: Final = frozenset({SyncStatus.RETRYABLE, SyncStatus.FAILED})

# Da -> verso. Le voci da lavorare vanno in uno qualsiasi degli altri stati (RETRYABLE incluso,
# se fallisce ancora); DOWNLOADED e MATCHED non si muovono; FAILED torna solo a PENDING e solo
# per `rearm_exhausted` (la "Verifica completa" riarma i tentativi esauriti).
ALLOWED_TRANSITIONS: Final[Mapping[SyncStatus, frozenset[SyncStatus]]] = {
    SyncStatus.PENDING: frozenset(
        {SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.RETRYABLE, SyncStatus.FAILED}
    ),
    SyncStatus.RETRYABLE: frozenset(
        {SyncStatus.DOWNLOADED, SyncStatus.MATCHED, SyncStatus.RETRYABLE, SyncStatus.FAILED}
    ),
    SyncStatus.DOWNLOADED: frozenset(),
    SyncStatus.MATCHED: frozenset(),
    SyncStatus.FAILED: frozenset({SyncStatus.PENDING}),
}


class InvalidTransitionError(ValueError):
    """La transizione di stato non è ammessa (la voce è già in uno stato finale)."""


def can_transition(old: SyncStatus, new: SyncStatus) -> bool:
    """Vero se una voce nello stato `old` può passare a `new`."""
    return new in ALLOWED_TRANSITIONS[old]


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Una voce del giornale: l'attività remota e a che punto è.

    Invarianti: `RETRYABLE` ha una causa ritentabile e `FAILED` una definitiva, ogni altro
    stato nessuna; `PENDING` non ha tentativi falliti, `RETRYABLE` e `FAILED` ne hanno almeno
    uno; `updated_at` è UTC al millisecondo. `failed_attempts` conta le sincronizzazioni in cui
    la voce è fallita.
    """

    remote: RemoteActivity
    status: SyncStatus
    updated_at: datetime
    failed_attempts: int = 0
    failure: FailureReason | None = None

    def __post_init__(self) -> None:
        check_utc(self.updated_at)
        if self.updated_at.microsecond % 1000:
            raise ValueError("l'istante deve essere al millisecondo")
        if self.failed_attempts < 0:
            raise ValueError("i tentativi falliti non possono essere negativi")
        if (self.status in _FAILURE_STATUSES) != (self.failure is not None):
            raise ValueError("solo gli stati di fallimento hanno una causa")
        if self.failure is not None and (self.failure in RETRYABLE_REASONS) != (
            self.status is SyncStatus.RETRYABLE
        ):
            raise ValueError("causa e stato non coerenti: ritentabile solo se RETRYABLE")
        if self.status is SyncStatus.PENDING and self.failed_attempts:
            raise ValueError("una voce da scaricare non ha tentativi falliti")
        if self.status in _FAILURE_STATUSES and self.failed_attempts < 1:
            raise ValueError("una voce fallita ha almeno un tentativo fallito")

    @property
    def garmin_id(self) -> GarminActivityId:
        """L'id Garmin della voce."""
        return self.remote.garmin_id


def pending_entry(remote: RemoteActivity, now: datetime) -> JournalEntry:
    """Voce nuova, da scaricare."""
    return JournalEntry(remote, SyncStatus.PENDING, now)


def _check_move(entry: JournalEntry, new: SyncStatus) -> None:
    if not can_transition(entry.status, new):
        raise InvalidTransitionError(f"{entry.status.value} -> {new.value}")


def mark_downloaded(entry: JournalEntry, now: datetime) -> JournalEntry:
    """La voce è scaricata, archiviata e indicizzata (conserva i tentativi falliti).

    Raises:
        InvalidTransitionError: la voce è già in uno stato finale.
    """
    _check_move(entry, SyncStatus.DOWNLOADED)
    return JournalEntry(entry.remote, SyncStatus.DOWNLOADED, now, entry.failed_attempts)


def mark_matched(entry: JournalEntry, now: datetime) -> JournalEntry:
    """La voce è abbinata a un'attività già importata da file: nessun download.

    Raises:
        InvalidTransitionError: la voce è già in uno stato finale.
    """
    _check_move(entry, SyncStatus.MATCHED)
    return JournalEntry(entry.remote, SyncStatus.MATCHED, now, entry.failed_attempts)


def mark_failed(
    entry: JournalEntry, reason: FailureReason, now: datetime, max_attempts: int
) -> JournalEntry:
    """La voce è fallita in questa sincronizzazione.

    Una causa definitiva dà `FAILED`. Una causa ritentabile dà `RETRYABLE`, finché i tentativi
    falliti (questo compreso) non arrivano a `max_attempts`: allora `FAILED` con
    `ATTEMPTS_EXHAUSTED`.

    Raises:
        InvalidTransitionError: la voce è già in uno stato finale.
        ValueError: `max_attempts` minore di 1.
    """
    if max_attempts < 1:
        raise ValueError("serve almeno un tentativo")
    attempts = entry.failed_attempts + 1
    if reason not in RETRYABLE_REASONS:
        status, cause = SyncStatus.FAILED, reason
    elif attempts < max_attempts:
        status, cause = SyncStatus.RETRYABLE, reason
    else:
        status, cause = SyncStatus.FAILED, FailureReason.ATTEMPTS_EXHAUSTED
    _check_move(entry, status)
    return JournalEntry(entry.remote, status, now, attempts, cause)


def rearm_exhausted(entry: JournalEntry, now: datetime) -> JournalEntry:
    """Riarma una voce fallita per tentativi esauriti: torna da scaricare, senza tentativi.

    Serve alla "Verifica completa": i tentativi consumati prima (anche per colpa della rete)
    non condannano per sempre un'attività valida. Le altre cause definitive (formato non
    supportato, file corrotto...) restano tali.

    Raises:
        InvalidTransitionError: la voce non è `FAILED` con `ATTEMPTS_EXHAUSTED`.
    """
    if (
        entry.status is not SyncStatus.FAILED
        or entry.failure is not FailureReason.ATTEMPTS_EXHAUSTED
    ):
        raise InvalidTransitionError(f"{entry.status.value} -> {SyncStatus.PENDING.value}")
    return JournalEntry(entry.remote, SyncStatus.PENDING, now)


# --- Cursore dell'elenco -----------------------------------------------------------------------


class ListingKind(Enum):
    """Quale elenco remoto sta percorrendo un cursore. Il valore è stabile (dati salvati)."""

    FULL = "full"  # prima sincronizzazione: tutto lo storico
    VERIFY = "verify"  # "Verifica completa": rielenca tutto, senza riscaricare
    INCREMENTAL = "incremental"  # sincronizzazioni successive: dalle più recenti


@dataclass(frozen=True, slots=True)
class ListingCursor:
    """Punto raggiunto in un elenco remoto: la prossima posizione e il totale dichiarato.

    Il totale serve solo all'avanzamento (può essere vecchio: Garmin riceve attività nuove
    mentre si elenca); non decide quando l'elenco finisce. `completed` dice che l'elenco è
    finito: non si avanza più, per ripartire si usa `ListingCursor.start()`.
    """

    next_offset: int
    declared_total: int | None
    completed: bool

    def __post_init__(self) -> None:
        if self.next_offset < 0 or (self.declared_total is not None and self.declared_total < 0):
            raise ValueError("posizione o totale negativi")

    @classmethod
    def start(cls) -> ListingCursor:
        """Cursore all'inizio dell'elenco."""
        return cls(0, None, False)


def is_known_page(page: ActivityPage, known_ids: Set[GarminActivityId]) -> bool:
    """Regola d'arresto dell'elenco incrementale: una pagina intera di id già noti.

    Vera se la pagina ha almeno una voce valida e tutte sono in `known_ids`. Una pagina senza
    voci valide non è "nota": o l'elenco è finito (lo dice `raw_count`) o erano tutte
    malformate.
    """
    return bool(page.items) and all(item.garmin_id in known_ids for item in page.items)


def advance_cursor(
    cursor: ListingCursor,
    page: ActivityPage,
    limit: int,
    known_ids: Set[GarminActivityId] | None = None,
) -> ListingCursor:
    """Cursore dopo aver elaborato `page`, chiesta con `limit` voci.

    L'elenco è finito se Garmin ha restituito meno di `limit` voci (nessuna compresa) oppure,
    solo se `known_ids` è dato (elenco incrementale), la pagina è tutta di id già noti
    (`is_known_page`). Il totale dichiarato **non** lo ferma: si fissa alla prima pagina e, se
    Garmin riceve un'attività nuova durante l'elenco, la più vecchia scivolerebbe oltre; basta
    una pagina corta o vuota (al più una richiesta in più quando il totale è un multiplo
    esatto di `limit`). Il tetto alle pagine è `MAX_LISTED_ACTIVITIES` del caso d'uso.

    Raises:
        ValueError: cursore già completato, pagina non alla posizione del cursore, `limit`
            fuori da 1..`MAX_PAGE_SIZE` o pagina più grande di `limit`.
    """
    if cursor.completed:
        raise ValueError("elenco già completato")
    if page.offset != cursor.next_offset:
        raise ValueError("la pagina non è alla posizione del cursore")
    if not 1 <= limit <= MAX_PAGE_SIZE or page.raw_count > limit:
        raise ValueError("limite di pagina non valido o superato")
    total = page.declared_total if page.declared_total is not None else cursor.declared_total
    next_offset = cursor.next_offset + page.raw_count
    done = page.raw_count < limit or (known_ids is not None and is_known_page(page, known_ids))
    return ListingCursor(next_offset, total, done)
