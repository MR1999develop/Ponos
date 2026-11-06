# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Testi della sincronizzazione con Garmin, in parole comuni: l'unico punto in cui un motivo,
un avanzamento o un esito diventa testo.

Contesto "Sync": `lupdate` trova solo contesto e testo scritti come letterali, quindi niente
alias di `QCoreApplication.translate`. Mai il testo di un'eccezione, mai un nome né un dato
dell'utente.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.domain import FailureReason
from ponos_coach.activities.sync import StopReason, SyncPhase, SyncProgress
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.sync_model import (
    DisabledReason,
    LoginFailure,
    RemainingUnit,
    SyncFinished,
    SyncNotStarted,
    SyncProblem,
    SyncResult,
    failure_counts,
    new_activity_count,
    round_remaining,
)


def disabled_reason_text(reason: DisabledReason) -> str:
    """Perché un comando del menu Garmin è spento (tooltip)."""
    return _DISABLED_REASONS[reason]()


def remaining_text(seconds: float) -> str:
    """Il tempo stimato in parole comode."""
    rounded = round_remaining(seconds)
    if rounded.unit is RemainingUnit.UNDER_A_MINUTE:
        return QCoreApplication.translate("Sync", "less than a minute left")
    if rounded.unit is RemainingUnit.MINUTES:
        return QCoreApplication.translate("Sync", "about %n minute(s) left", "", rounded.amount)
    return QCoreApplication.translate("Sync", "about %n hour(s) left", "", rounded.amount)


def progress_text(progress: SyncProgress) -> str:
    """Fase, contatore e, se c'è, tempo stimato: per la barra di stato."""
    if progress.phase is SyncPhase.LISTING:
        if progress.total:
            # {done}: activities listed so far; %n: activities Garmin has declared.
            text = QCoreApplication.translate(
                "Sync", "Listing activities: {done} of %n", "", progress.total
            ).format(done=progress.done)
        else:
            text = QCoreApplication.translate("Sync", "Listing activities: {done}").format(
                done=progress.done
            )
    else:
        # {step}: the activity being downloaded; %n: activities to download.
        text = QCoreApplication.translate(
            "Sync", "Downloading activity {step} of %n", "", progress.total or 0
        ).format(step=min(progress.done + 1, progress.total or 0))
    if progress.remaining_s is None or progress.phase is SyncPhase.LISTING:
        return text
    # {progress}: the step above; {time}: the estimated time left.
    return QCoreApplication.translate("Sync", "{progress} - {time}").format(
        progress=text, time=remaining_text(progress.remaining_s)
    )


def _timeout_text() -> str:
    return QCoreApplication.translate("Sync", "Garmin did not answer; it will be tried again.")


_FAILURE_REASONS: dict[FailureReason, Callable[[], str]] = {
    FailureReason.TRANSIENT_ERROR: _timeout_text,
    FailureReason.RATE_LIMITED: lambda: QCoreApplication.translate(
        "Sync", "Garmin asked to slow down; it will be tried again."
    ),
    FailureReason.UNSUPPORTED_FORMAT: lambda: QCoreApplication.translate(
        "Sync", "the file format is not supported (for example TCX)"
    ),
    FailureReason.CORRUPT_FILE: lambda: QCoreApplication.translate("Sync", "the file is damaged"),
    FailureReason.FILE_TOO_LARGE: lambda: QCoreApplication.translate(
        "Sync", "the file is too large"
    ),
    FailureReason.NOT_AN_ACTIVITY: lambda: QCoreApplication.translate(
        "Sync", "the file is not an activity"
    ),
    FailureReason.ARCHIVE_INVALID: lambda: QCoreApplication.translate(
        "Sync", "the downloaded package is not valid"
    ),
    FailureReason.REMOTE_REJECTED: lambda: QCoreApplication.translate(
        "Sync", "Garmin does not provide the original file"
    ),
    FailureReason.ATTEMPTS_EXHAUSTED: lambda: QCoreApplication.translate(
        "Sync", "it failed too many times"
    ),
}


def failure_reason_text(reason: FailureReason) -> str:
    """Perché una voce non è stata scaricata, in parole comuni."""
    return _FAILURE_REASONS[reason]()


_STOP_REASONS: dict[StopReason, Callable[[], str]] = {
    StopReason.CANCELLED: lambda: QCoreApplication.translate(
        "Sync", "Synchronisation stopped. Nothing is lost: it resumes where it left off."
    ),
    StopReason.AUTHENTICATION_REQUIRED: lambda: QCoreApplication.translate(
        "Sync", "Garmin needs you to sign in again."
    ),
    StopReason.RATE_LIMITED: lambda: QCoreApplication.translate(
        "Sync", "Garmin is limiting requests. Try again in a while."
    ),
    StopReason.SERVICE_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync", "Garmin is not answering. Check your connection and try again."
    ),
    StopReason.REMOTE_ERROR: lambda: QCoreApplication.translate(
        "Sync", "Garmin sent an answer that Ponos Coach cannot read."
    ),
    StopReason.STORAGE_ERROR: lambda: QCoreApplication.translate(
        "Sync", "The profile folder cannot be written. Check that the disk is not full."
    ),
    StopReason.LISTING_LIMIT: lambda: QCoreApplication.translate(
        "Sync", "Garmin listed more activities than Ponos Coach accepts."
    ),
}


def stop_reason_text(reason: StopReason) -> str:
    """Perché la sincronizzazione si è fermata, e che cosa fare."""
    return _STOP_REASONS[reason]()


_PROBLEMS: dict[SyncProblem, Callable[[], str]] = {
    SyncProblem.NOT_LINKED: lambda: QCoreApplication.translate(
        "Sync", "No Garmin account is connected to this profile."
    ),
    SyncProblem.AUTH_REQUIRED: lambda: QCoreApplication.translate(
        "Sync", "Garmin needs you to sign in again."
    ),
    SyncProblem.KEYCHAIN_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync", "The system keychain is not available, so the saved sign-in cannot be read."
    ),
    SyncProblem.RATE_LIMITED: lambda: QCoreApplication.translate(
        "Sync", "Garmin is limiting requests. Try again in a while."
    ),
    SyncProblem.SERVICE_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync", "Garmin is not answering. Check your connection and try again."
    ),
    SyncProblem.PROFILE_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync", "The profile data could not be opened for synchronisation."
    ),
    SyncProblem.UNEXPECTED: lambda: QCoreApplication.translate(
        "Sync", "Something went wrong. If it persists, use Help > Open the log folder."
    ),
}


def problem_text(problem: SyncProblem) -> str:
    """Perché un giro non è partito, e che cosa fare."""
    return _PROBLEMS[problem]()


def headline_text(result: SyncResult) -> str:
    """Una riga che dice com'è andata: per la barra di stato e per il titolo del riepilogo."""
    if isinstance(result, SyncNotStarted):
        return QCoreApplication.translate("Sync", "Synchronisation did not start")
    outcome = result.outcome
    if outcome.stop is not None:
        return QCoreApplication.translate("Sync", "Synchronisation stopped")
    new = new_activity_count(result)
    if new == 0 and outcome.matched == 0:
        return QCoreApplication.translate("Sync", "Garmin is up to date")
    if outcome.matched == 0:
        return QCoreApplication.translate("Sync", "Synchronised: %n new activity(ies)", "", new)
    # %n: new activities; {matched}: activities already in the profile from imported files.
    return QCoreApplication.translate(
        "Sync",
        "Synchronised: %n new activity(ies), {matched} matched with imported files",
        "",
        new,
    ).format(matched=outcome.matched)


def summary_lines(result: SyncResult) -> tuple[str, ...]:
    """Il riepilogo, una riga per cosa: nuove, abbinate, non scaricate (con il motivo), arresto."""
    if isinstance(result, SyncNotStarted):
        return (problem_text(result.problem),)
    outcome = result.outcome
    lines: list[str] = []
    if outcome.downloaded:
        lines.append(
            QCoreApplication.translate(
                "Sync", "%n new activity(ies) downloaded", "", outcome.downloaded
            )
        )
    if outcome.matched:
        lines.append(
            QCoreApplication.translate(
                "Sync", "%n activity(ies) matched with files you imported", "", outcome.matched
            )
        )
    lines.extend(_failure_lines(result))
    if outcome.stop is not None:
        lines.append(stop_reason_text(outcome.stop))
    return tuple(lines)


def _failure_lines(result: SyncResult) -> list[str]:
    counts = failure_counts(result)
    if not counts:
        return []
    total = sum(count for _, count in counts)
    lines = [QCoreApplication.translate("Sync", "%n activity(ies) not downloaded:", "", total)]
    for reason, count in counts:
        # {reason}: why they were not downloaded; {count}: how many activities.
        lines.append(
            QCoreApplication.translate("Sync", "  {reason}: {count}").format(
                reason=failure_reason_text(reason), count=count
            )
        )
    return lines


def summary_message(result: SyncResult) -> ErrorMessage:
    """Titolo e righe del riepilogo con il suo rimedio, per la finestra del riepilogo."""
    lines = summary_lines(result)
    what = "\n".join(lines)
    if isinstance(result, SyncFinished) and result.outcome.completed:
        remedy = QCoreApplication.translate(
            "Sync", "The activities not downloaded stay in the list and are not lost."
        )
    else:
        remedy = QCoreApplication.translate(
            "Sync", "Synchronise again when you are ready: it resumes where it left off."
        )
    return ErrorMessage(headline_text(result), what, remedy)


def notification_texts(count: int) -> tuple[str, str]:
    """Titolo e testo della notifica di sistema per `count` attività nuove."""
    return (
        QCoreApplication.translate("Sync", "New activities from Garmin"),
        QCoreApplication.translate("Sync", "%n new activity(ies) downloaded", "", count),
    )


_LOGIN_FAILURES: dict[LoginFailure, Callable[[], str]] = {
    LoginFailure.INVALID_CREDENTIALS: lambda: QCoreApplication.translate(
        "Sync", "Garmin did not accept the email, password or code. Check them and try again."
    ),
    LoginFailure.RATE_LIMITED: lambda: QCoreApplication.translate(
        "Sync", "Garmin is limiting sign-in attempts. Wait a few minutes, then try again."
    ),
    LoginFailure.SERVICE_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync", "Ponos Coach could not reach Garmin. Check your connection and try again."
    ),
    LoginFailure.KEYCHAIN_UNAVAILABLE: lambda: QCoreApplication.translate(
        "Sync",
        "The sign-in worked, but the system keychain could not keep it, "
        "so the account was not connected.",
    ),
    LoginFailure.OTHER: lambda: QCoreApplication.translate(
        "Sync", "Something went wrong. Try again; if it persists, use Help > Open the log folder."
    ),
}


def login_failure_text(failure: LoginFailure) -> str:
    """Perché il collegamento non è riuscito, e che cosa fare."""
    return _LOGIN_FAILURES[failure]()


def _no_profile() -> str:
    return QCoreApplication.translate("Sync", "Create or open a profile first.")


def _keychain() -> str:
    return QCoreApplication.translate(
        "Sync",
        "The system keychain is not available, so a Garmin account cannot be connected. "
        "You can still import files.",
    )


def _checking() -> str:
    return QCoreApplication.translate("Sync", "Ponos Coach is checking the Garmin account.")


def _already_linked() -> str:
    return QCoreApplication.translate(
        "Sync", "A Garmin account is already connected. Disconnect it to use another."
    )


def _not_linked() -> str:
    return QCoreApplication.translate("Sync", "Connect a Garmin account first.")


def _needs_reconnect() -> str:
    return QCoreApplication.translate(
        "Sync", "Garmin needs you to sign in again. Use Garmin > Connect Garmin account."
    )


def _busy() -> str:
    return QCoreApplication.translate(
        "Sync", "Wait for the current operation to finish, or stop it first."
    )


def _not_syncing() -> str:
    return QCoreApplication.translate("Sync", "No synchronisation is running.")


_DISABLED_REASONS: dict[DisabledReason, Callable[[], str]] = {
    DisabledReason.NO_PROFILE: _no_profile,
    DisabledReason.KEYCHAIN_UNAVAILABLE: _keychain,
    DisabledReason.CHECKING: _checking,
    DisabledReason.ALREADY_LINKED: _already_linked,
    DisabledReason.NOT_LINKED: _not_linked,
    DisabledReason.NEEDS_RECONNECT: _needs_reconnect,
    DisabledReason.BUSY: _busy,
    DisabledReason.NOT_SYNCING: _not_syncing,
}
