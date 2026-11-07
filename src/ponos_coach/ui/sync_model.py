# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of the Garmin synchronisation: testable without a window.

The decisions live here: what state the account is in, which commands are available (and why
not), what to summarise at the end of a round and whether a notification is needed. `sync_text`
chooses the texts,
the view (`garmin_ui`) only shows.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import RETRYABLE_REASONS, FailureReason
from ponos_coach.activities.sync import StopReason, SyncMode, SyncOutcome


class AccountStatus(Enum):
    """What is known about the open profile's Garmin account."""

    UNKNOWN = "unknown"  # no profile, or not yet checked
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"  # the system keychain cannot be used
    NOT_LINKED = "not_linked"  # no account connected
    LINKED = "linked"  # tokens saved
    NEEDS_RECONNECT = "needs_reconnect"  # tokens expired, revoked or unreadable


class Operation(Enum):
    """The job in progress with the account (one at a time)."""

    CHECK = "check"  # reading the saved tokens
    LOGIN = "login"
    LOGOUT = "logout"
    SYNC = "sync"


class LoginFailure(Enum):
    """Why a connection did not succeed (if the user cancelled it is not a failure)."""

    INVALID_CREDENTIALS = "invalid_credentials"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    MFA_TIMED_OUT = "mfa_timed_out"  # the code did not arrive in time
    OTHER = "other"


class LoginEnd(Enum):
    """How a connection that did not fail ended."""

    CONNECTED = "connected"
    CANCELLED = "cancelled"  # the user cancelled (MFA code or dialog): nothing was saved


type LoginResult = LoginEnd | LoginFailure


class SyncProblem(Enum):
    """Why a synchronisation did not even start (or ended unexpectedly)."""

    NOT_LINKED = "not_linked"
    AUTH_REQUIRED = "auth_required"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class SyncFinished:
    """The round reached the use case: `outcome` says how it went (even if interrupted)."""

    outcome: SyncOutcome
    mode: SyncMode
    automatic: bool


@dataclass(frozen=True, slots=True)
class SyncNotStarted:
    """The round produced no outcome: `problem` says why."""

    problem: SyncProblem
    mode: SyncMode
    automatic: bool


type SyncResult = SyncFinished | SyncNotStarted


# --- What happened ---------------------------------------------------------------------


def new_activity_count(result: SyncResult) -> int:
    """How many new activities entered the profile (downloaded; matched ones were already there)."""
    return result.outcome.downloaded if isinstance(result, SyncFinished) else 0


def needs_reconnect(result: SyncResult) -> bool:
    """Garmin asks for a new login."""
    if isinstance(result, SyncNotStarted):
        return result.problem is SyncProblem.AUTH_REQUIRED
    return result.outcome.stop is StopReason.AUTHENTICATION_REQUIRED


def account_status_after(result: SyncResult, current: AccountStatus) -> AccountStatus:
    """The account state after a round: failures that say something about the account change it."""
    if needs_reconnect(result):
        return AccountStatus.NEEDS_RECONNECT
    if isinstance(result, SyncNotStarted):
        if result.problem is SyncProblem.NOT_LINKED:
            return AccountStatus.NOT_LINKED
        if result.problem is SyncProblem.KEYCHAIN_UNAVAILABLE:
            return AccountStatus.KEYCHAIN_UNAVAILABLE
    return current


def needs_attention(result: SyncResult) -> bool:
    """The summary deserves a window: something did not go as expected.

    A round interrupted by the user or finished with everything in order is told in the status bar.
    """
    if isinstance(result, SyncNotStarted):
        return True
    outcome = result.outcome
    return bool(outcome.failures) or outcome.stop not in (None, StopReason.CANCELLED)


def failure_counts(result: SyncResult) -> tuple[tuple[FailureReason, int], ...]:
    """The items not downloaded by cause, from the most frequent (ties in enum order)."""
    if isinstance(result, SyncNotStarted):
        return ()
    counts = result.outcome.failures_by_reason()
    order = {reason: position for position, reason in enumerate(FailureReason)}
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], order[item[0]])))


def has_retryable_failures(result: SyncResult) -> bool:
    """At least one item failed for a transient cause: it stays in the list and is retried."""
    return any(reason in RETRYABLE_REASONS for reason, _ in failure_counts(result))


def has_definitive_failures(result: SyncResult) -> bool:
    """At least one item failed for a permanent cause: it is not retried on its own."""
    return any(reason not in RETRYABLE_REASONS for reason, _ in failure_counts(result))


# --- Which commands can be given --------------------------------------------------------------


class DisabledReason(Enum):
    """Why a command is off; the view translates it into the tooltip."""

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
    """A command that is available or not, with the reason."""

    reason: DisabledReason | None = None

    @property
    def enabled(self) -> bool:
        """True if the command can be given."""
        return self.reason is None


_AVAILABLE = Availability()


@dataclass(frozen=True, slots=True)
class CommandInputs:
    """What the account commands depend on."""

    profile_open: bool
    account: AccountStatus
    operation: Operation | None
    closing: bool


@dataclass(frozen=True, slots=True)
class SyncCommands:
    """Availability of the Garmin menu commands."""

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
    """What turns off all the account commands except Stop."""
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
    """The toggle applies to the open profile and does not depend on the job in progress."""
    if not inputs.profile_open:
        return DisabledReason.NO_PROFILE
    if inputs.account is AccountStatus.KEYCHAIN_UNAVAILABLE:
        return DisabledReason.KEYCHAIN_UNAVAILABLE
    return None


def commands_for(inputs: CommandInputs) -> SyncCommands:
    """Which Garmin menu commands are available and, for the others, why not."""
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
    """In which unit the estimated time is expressed."""

    UNDER_A_MINUTE = "under_a_minute"
    MINUTES = "minutes"
    HOURS = "hours"


@dataclass(frozen=True, slots=True)
class RemainingTime:
    """The estimated time rounded to a convenient figure: `unit` and how many units."""

    unit: RemainingUnit
    amount: int


_SECONDS_PER_MINUTE = 60
_MINUTES_PER_HOUR = 60
_HOURS_FROM_MINUTES = 90


def round_remaining(seconds: float) -> RemainingTime:
    """Estimated time in convenient words: less than a minute, minutes (rounded up), then hours.

    Raises:
        ValueError: `seconds` is not finite or is negative.
    """
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("the estimated time must be finite and non-negative")
    if seconds < _SECONDS_PER_MINUTE:
        return RemainingTime(RemainingUnit.UNDER_A_MINUTE, 0)
    minutes = math.ceil(seconds / _SECONDS_PER_MINUTE)
    if minutes < _HOURS_FROM_MINUTES:
        return RemainingTime(RemainingUnit.MINUTES, minutes)
    return RemainingTime(RemainingUnit.HOURS, round(minutes / _MINUTES_PER_HOUR))
