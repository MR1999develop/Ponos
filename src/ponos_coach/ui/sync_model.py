# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modello di presentazione della sincronizzazione con Garmin: testabile senza finestra.

Qui stanno le decisioni: in che stato è l'account, quali comandi sono disponibili (e perché
no), che cosa riassumere a fine giro e se serve una notifica. I testi li sceglie `sync_text`,
la vista (`garmin_ui`) mostra soltanto.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import RETRYABLE_REASONS, FailureReason
from ponos_coach.activities.sync import StopReason, SyncMode, SyncOutcome


class AccountStatus(Enum):
    """Che cosa si sa dell'account Garmin del profilo aperto."""

    UNKNOWN = "unknown"  # nessun profilo, o non ancora controllato
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"  # il portachiavi di sistema non si può usare
    NOT_LINKED = "not_linked"  # nessun account collegato
    LINKED = "linked"  # token salvati
    NEEDS_RECONNECT = "needs_reconnect"  # token scaduti, revocati o illeggibili


class Operation(Enum):
    """Il lavoro in corso con l'account (uno alla volta)."""

    CHECK = "check"  # lettura dei token salvati
    LOGIN = "login"
    LOGOUT = "logout"
    SYNC = "sync"


class LoginFailure(Enum):
    """Perché un collegamento non è riuscito (se l'utente ha annullato non è un fallimento)."""

    INVALID_CREDENTIALS = "invalid_credentials"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    MFA_TIMED_OUT = "mfa_timed_out"  # il codice non è arrivato in tempo
    OTHER = "other"


class LoginEnd(Enum):
    """Come è finito un collegamento che non è fallito."""

    CONNECTED = "connected"
    CANCELLED = "cancelled"  # l'utente ha annullato (codice MFA o dialogo): nulla è stato salvato


type LoginResult = LoginEnd | LoginFailure


class SyncProblem(Enum):
    """Perché una sincronizzazione non è nemmeno partita (o è finita in modo imprevisto)."""

    NOT_LINKED = "not_linked"
    AUTH_REQUIRED = "auth_required"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class SyncFinished:
    """Il giro è arrivato al caso d'uso: `outcome` dice com'è andato (anche se interrotto)."""

    outcome: SyncOutcome
    mode: SyncMode
    automatic: bool


@dataclass(frozen=True, slots=True)
class SyncNotStarted:
    """Il giro non ha prodotto un esito: `problem` dice perché."""

    problem: SyncProblem
    mode: SyncMode
    automatic: bool


type SyncResult = SyncFinished | SyncNotStarted


# --- Che cosa è successo ---------------------------------------------------------------------


def new_activity_count(result: SyncResult) -> int:
    """Quante attività nuove sono entrate nel profilo (scaricate; le abbinate c'erano già)."""
    return result.outcome.downloaded if isinstance(result, SyncFinished) else 0


def needs_reconnect(result: SyncResult) -> bool:
    """Garmin chiede un nuovo accesso."""
    if isinstance(result, SyncNotStarted):
        return result.problem is SyncProblem.AUTH_REQUIRED
    return result.outcome.stop is StopReason.AUTHENTICATION_REQUIRED


def account_status_after(result: SyncResult, current: AccountStatus) -> AccountStatus:
    """Lo stato dell'account dopo un giro: i guasti che dicono qualcosa sull'account lo cambiano."""
    if needs_reconnect(result):
        return AccountStatus.NEEDS_RECONNECT
    if isinstance(result, SyncNotStarted):
        if result.problem is SyncProblem.NOT_LINKED:
            return AccountStatus.NOT_LINKED
        if result.problem is SyncProblem.KEYCHAIN_UNAVAILABLE:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
    return current


def needs_attention(result: SyncResult) -> bool:
    """Il riepilogo merita una finestra: qualcosa non è andato come previsto.

    Un giro interrotto dall'utente o finito con tutto in ordine si dice nella barra di stato.
    """
    if isinstance(result, SyncNotStarted):
        return True
    outcome = result.outcome
    return bool(outcome.failures) or outcome.stop not in (None, StopReason.CANCELLED)


def failure_counts(result: SyncResult) -> tuple[tuple[FailureReason, int], ...]:
    """Le voci non scaricate per causa, dalla più frequente (a parità, nell'ordine dell'enum)."""
    if isinstance(result, SyncNotStarted):
        return ()
    counts = result.outcome.failures_by_reason()
    order = {reason: position for position, reason in enumerate(FailureReason)}
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], order[item[0]])))


def has_retryable_failures(result: SyncResult) -> bool:
    """Almeno una voce è fallita per una causa transitoria: resta in elenco e si ritenta."""
    return any(reason in RETRYABLE_REASONS for reason, _ in failure_counts(result))


def has_definitive_failures(result: SyncResult) -> bool:
    """Almeno una voce è fallita per una causa definitiva: non si ritenta da sola."""
    return any(reason not in RETRYABLE_REASONS for reason, _ in failure_counts(result))


# --- Quali comandi si possono dare --------------------------------------------------------------


class DisabledReason(Enum):
    """Perché un comando è spento; la vista lo traduce nel tooltip."""

    NO_PROFILE = "no_profile"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    CHECKING = "checking"
    ALREADY_LINKED = "already_linked"
    NOT_LINKED = "not_linked"
    NEEDS_RECONNECT = "needs_reconnect"
    BUSY = "busy"
    NOT_SYNCING = "not_syncing"


@dataclass(frozen=True, slots=True)
class Availability:
    """Un comando disponibile oppure no, con il motivo."""

    reason: DisabledReason | None = None

    @property
    def enabled(self) -> bool:
        """Vero se il comando si può dare."""
        return self.reason is None


_AVAILABLE = Availability()


@dataclass(frozen=True, slots=True)
class CommandInputs:
    """Ciò da cui dipendono i comandi dell'account."""

    profile_open: bool
    account: AccountStatus
    operation: Operation | None
    closing: bool


@dataclass(frozen=True, slots=True)
class SyncCommands:
    """Disponibilità dei comandi del menu Garmin."""

    connect: Availability
    disconnect: Availability
    sync: Availability
    verify: Availability
    stop: Availability
    auto_sync: Availability


def _busy_reason(inputs: CommandInputs) -> DisabledReason | None:
    if inputs.closing:
        return DisabledReason.BUSY
    if inputs.operation is Operation.CHECK:
        return DisabledReason.CHECKING
    if inputs.operation is not None:
        return DisabledReason.BUSY
    return None


def _common_reason(inputs: CommandInputs) -> DisabledReason | None:
    """Ciò che spegne tutti i comandi dell'account tranne Interrompi."""
    if not inputs.profile_open:
        return DisabledReason.NO_PROFILE
    if inputs.account is AccountStatus.KEYCHAIN_UNAVAILABLE:
        return DisabledReason.KEYCHAIN_UNAVAILABLE
    return _busy_reason(inputs)


def _connect_reason(inputs: CommandInputs) -> DisabledReason | None:
    common = _common_reason(inputs)
    if common is not None:
        return common
    return DisabledReason.ALREADY_LINKED if inputs.account is AccountStatus.LINKED else None


def _disconnect_reason(inputs: CommandInputs) -> DisabledReason | None:
    common = _common_reason(inputs)
    if common is not None:
        return common
    linked = inputs.account in (AccountStatus.LINKED, AccountStatus.NEEDS_RECONNECT)
    return None if linked else DisabledReason.NOT_LINKED


def _sync_reason(inputs: CommandInputs) -> DisabledReason | None:
    common = _common_reason(inputs)
    if common is not None:
        return common
    if inputs.account is AccountStatus.NEEDS_RECONNECT:
        return DisabledReason.NEEDS_RECONNECT
    return None if inputs.account is AccountStatus.LINKED else DisabledReason.NOT_LINKED


def _auto_sync_reason(inputs: CommandInputs) -> DisabledReason | None:
    """L'interruttore vale per il profilo aperto e non dipende dal lavoro in corso."""
    if not inputs.profile_open:
        return DisabledReason.NO_PROFILE
    if inputs.account is AccountStatus.KEYCHAIN_UNAVAILABLE:
        return DisabledReason.KEYCHAIN_UNAVAILABLE
    return None


def commands_for(inputs: CommandInputs) -> SyncCommands:
    """Quali comandi del menu Garmin sono disponibili e, per gli altri, perché no."""
    sync_reason = _sync_reason(inputs)
    stoppable = inputs.operation is Operation.SYNC and not inputs.closing
    return SyncCommands(
        connect=Availability(_connect_reason(inputs)),
        disconnect=Availability(_disconnect_reason(inputs)),
        sync=Availability(sync_reason),
        verify=Availability(sync_reason),
        stop=_AVAILABLE if stoppable else Availability(DisabledReason.NOT_SYNCING),
        auto_sync=Availability(_auto_sync_reason(inputs)),
    )


# --- Avanzamento ----------------------------------------------------------------------------------


class RemainingUnit(Enum):
    """In che unità si dice il tempo stimato."""

    UNDER_A_MINUTE = "under_a_minute"
    MINUTES = "minutes"
    HOURS = "hours"


@dataclass(frozen=True, slots=True)
class RemainingTime:
    """Il tempo stimato arrotondato a una cifra comoda: `unit` e quante unità."""

    unit: RemainingUnit
    amount: int


_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_FROM_MINUTES = 90


def round_remaining(seconds: float) -> RemainingTime:
    """Tempo stimato in parole comode: meno di un minuto, minuti (per eccesso), poi ore.

    Raises:
        ValueError: `seconds` non finito o negativo.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("il tempo stimato deve essere finito e non negativo")
    if seconds < _SECONDS_PER_MINUTE:
        return RemainingTime(RemainingUnit.UNDER_A_MINUTE, 0)
    minutes = math.ceil(seconds / _SECONDS_PER_MINUTE)
    if minutes < _HOURS_FROM_MINUTES:
        return RemainingTime(RemainingUnit.MINUTES, minutes)
    return RemainingTime(RemainingUnit.HOURS, round(minutes / _MINUTES_PER_HOUR))
