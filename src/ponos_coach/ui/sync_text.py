# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin synchronisation texts, in plain words: the only place where a reason,
progress or outcome becomes text.

Context "Sync": `lupdate` only finds context and text written as literals, so no
aliases of `QCoreApplication.translate`. Never an exception's text, never a name or data
of the user.
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
    has_definitive_failures,
    has_retryable_failures,
    new_activity_count,
    round_remaining,
)


def disabled_reason_text(reason: DisabledReason) -> str:
    """Why a Garmin menu command is off (tooltip)."""
    return _DISABLED_REASONS[reason]()


def remaining_text(seconds: float) -> str:
    """The estimated time in convenient words."""
    rounded = round_remaining(seconds)
    if rounded.unit is RemainingUnit.UNDER_A_MINUTE:
        return QCoreApplication.translate("Sync", "less than a minute left")
    if rounded.unit is RemainingUnit.MINUTES:
        return QCoreApplication.translate("Sync", "about %n min left", "", rounded.amount)
    return QCoreApplication.translate("Sync", "about %n h left", "", rounded.amount)


def progress_text(progress: SyncProgress) -> str:
    """Phase, counter and, if present, estimated time: for the status bar."""
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
        "Sync", "Garmin did not provide a valid original file for this activity"
    ),
    FailureReason.ATTEMPTS_EXHAUSTED: lambda: QCoreApplication.translate(
        "Sync", "it failed too many times"
    ),
}


def failure_reason_text(reason: FailureReason) -> str:
    """Why an item was not downloaded, in plain words."""
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
        "Sync",
        "The profile was busy with another task or could not be written. "
        "Try again; if it persists, check that the disk is not full.",
    ),
    StopReason.LISTING_LIMIT: lambda: QCoreApplication.translate(
        "Sync", "Garmin listed more activities than Ponos Coach accepts."
    ),
}


def stop_reason_text(reason: StopReason) -> str:
    """Why the synchronisation stopped, and what to do."""
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
    """Why a round did not start, and what to do."""
    return _PROBLEMS[problem]()


def headline_text(result: SyncResult) -> str:
    """A line saying how it went: for the status bar and the summary title."""
    if isinstance(result, SyncNotStarted):
        return QCoreApplication.translate("Sync", "Synchronisation did not start")
    outcome = result.outcome
    if outcome.stop is not None:
        return QCoreApplication.translate("Sync", "Synchronisation stopped")
    new = new_activity_count(result)
    if new == 0 and outcome.matched == 0:
        return QCoreApplication.translate("Sync", "Garmin is up to date")
    if outcome.matched == 0:
        return QCoreApplication.translate("Sync", "New activities synchronised: %n", "", new)
    # %n: new activities; {matched}: activities already in the profile from imported files.
    return QCoreApplication.translate(
        "Sync",
        "New activities synchronised: %n, matched with imported files: {matched}",
        "",
        new,
    ).format(matched=outcome.matched)


def summary_lines(result: SyncResult) -> tuple[str, ...]:
    """The summary, one line per item: new, matched, not downloaded (with the reason), stop."""
    if isinstance(result, SyncNotStarted):
        return (problem_text(result.problem),)
    outcome = result.outcome
    lines: list[str] = []
    if outcome.downloaded:
        lines.append(
            QCoreApplication.translate(
                "Sync", "New activities downloaded: %n", "", outcome.downloaded
            )
        )
    if outcome.matched:
        lines.append(
            QCoreApplication.translate(
                "Sync", "Activities matched with files you imported: %n", "", outcome.matched
            )
        )
    if outcome.already_present:
        lines.append(
            QCoreApplication.translate(
                "Sync", "Activities already in the profile: %n", "", outcome.already_present
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
    lines = [QCoreApplication.translate("Sync", "Activities not downloaded: %n", "", total)]
    for reason, count in counts:
        # {reason}: why they were not downloaded; {count}: how many activities.
        lines.append(
            QCoreApplication.translate("Sync", "  {reason}: {count}").format(
                reason=failure_reason_text(reason), count=count
            )
        )
    return lines


def summary_message(result: SyncResult) -> ErrorMessage:
    """Title and lines of the summary with its remedy, for the summary window."""
    lines = summary_lines(result)
    what = "\n".join(lines)
    remedy = "\n".join(_remedy_lines(result))
    return ErrorMessage(headline_text(result), what, remedy)


def _remedy_lines(result: SyncResult) -> list[str]:
    """What to do: resume the round if it stopped, and the fate of the failed items
    (transient ones stay in the list, permanent ones do not)."""
    remedies: list[str] = []
    if not isinstance(result, SyncFinished) or not result.outcome.completed:
        remedies.append(
            QCoreApplication.translate(
                "Sync", "Synchronise again when you are ready: it resumes where it left off."
            )
        )
    if has_retryable_failures(result):
        remedies.append(
            QCoreApplication.translate(
                "Sync",
                "The activities that failed for a temporary reason stay in the list "
                "and are tried again next time.",
            )
        )
    if has_definitive_failures(result):
        remedies.append(
            QCoreApplication.translate(
                "Sync",
                "The activities that failed for a lasting reason are not tried again.",
            )
        )
    return remedies


def notification_texts(count: int) -> tuple[str, str]:
    """Title and text of the system notification for `count` new activities."""
    return (
        QCoreApplication.translate("Sync", "New activities from Garmin"),
        QCoreApplication.translate("Sync", "New activities downloaded: %n", "", count),
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
    LoginFailure.MFA_TIMED_OUT: lambda: QCoreApplication.translate(
        "Sync", "Time ran out waiting for the verification code. Connect the account again."
    ),
    LoginFailure.OTHER: lambda: QCoreApplication.translate(
        "Sync", "Something went wrong. Try again; if it persists, use Help > Open the log folder."
    ),
}


def login_failure_text(failure: LoginFailure) -> str:
    """Why the connection did not succeed, and what to do."""
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
