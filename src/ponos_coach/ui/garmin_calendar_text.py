# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin calendar texts, in plain words: the only place where a reason, progress or outcome
becomes text.

Context "GarminCalendar": `lupdate` only finds context and text written as literals, so no
aliases of `QCoreApplication.translate`. Never an exception's text. A workout's day and name are
shown (they are what the user chose or the app wrote), but never health data.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.planning.calendar_use_cases import (
    MAX_WORKOUTS_PER_SEND,
    Failure,
    FailureReason,
    StopReason,
)
from ponos_coach.planning.domain.workouts import ScheduledWorkout
from ponos_coach.ui.garmin_calendar_model import (
    CalendarNotStarted,
    CalendarProblem,
    CalendarProgress,
    CalendarReason,
    CalendarResult,
    Deleted,
    JobKind,
    Listed,
    Restored,
    Sent,
    needs_attention,
    stop_of,
)

_MAX_FAILURE_LINES = 8
_MAX_LISTED_WORKOUTS = 10


def day_text(day: date) -> str:
    """A day in the user's locale."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def workout_line(item: ScheduledWorkout) -> str:
    """One workout for a list: its day and name."""
    # {day}: the day; {name}: the workout's name.
    return QCoreApplication.translate("GarminCalendar", "{day}: {name}").format(
        day=day_text(item.day), name=item.name
    )


# --- Why a command is off ----------------------------------------------------------------------


def reason_text(reason: CalendarReason) -> str:
    """Why a calendar command is off (tooltip)."""
    return _REASONS[reason]()


def _reasons() -> dict[CalendarReason, Callable[[], str]]:
    return {
        CalendarReason.NO_PROFILE: lambda: QCoreApplication.translate(
            "GarminCalendar", "Create or open a profile first."
        ),
        CalendarReason.KEYCHAIN_UNAVAILABLE: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "The system keychain cannot be used, so no Garmin account can be connected.",
        ),
        CalendarReason.CHECKING: lambda: QCoreApplication.translate(
            "GarminCalendar", "Checking the Garmin account; try again in a moment."
        ),
        CalendarReason.BUSY: lambda: QCoreApplication.translate(
            "GarminCalendar", "Another Garmin task is running; wait for it or stop it."
        ),
        CalendarReason.NOT_LINKED: lambda: QCoreApplication.translate(
            "GarminCalendar", "Connect a Garmin account first."
        ),
        CalendarReason.NEEDS_RECONNECT: lambda: QCoreApplication.translate(
            "GarminCalendar", "Garmin asks you to connect again."
        ),
        CalendarReason.UNSUPPORTED: lambda: QCoreApplication.translate(
            "GarminCalendar", "This Garmin connection cannot manage workouts."
        ),
        CalendarReason.NO_PLAN: lambda: QCoreApplication.translate(
            "GarminCalendar", "There is no plan yet: add a goal first."
        ),
        CalendarReason.NO_SESSIONS: lambda: QCoreApplication.translate(
            "GarminCalendar", "The plan has no sessions from today on."
        ),
        CalendarReason.NO_DAY_SELECTED: lambda: QCoreApplication.translate(
            "GarminCalendar", "Select a day in the calendar first."
        ),
        CalendarReason.NO_SESSION_ON_DAY: lambda: QCoreApplication.translate(
            "GarminCalendar", "The selected day has no planned session."
        ),
        CalendarReason.NO_SESSIONS_THAT_WEEK: lambda: QCoreApplication.translate(
            "GarminCalendar", "The selected week has no planned sessions."
        ),
        CalendarReason.DAY_PASSED: lambda: QCoreApplication.translate(
            "GarminCalendar", "Only sessions of today or later can be sent."
        ),
    }


_REASONS = _reasons()


# --- Progress ----------------------------------------------------------------------------------


def progress_text(progress: CalendarProgress) -> str:
    """What the job is doing, for the status bar."""
    total = progress.total or 0
    step = min(progress.done + 1, total) if total else progress.done + 1
    # {step}: the workout being handled; %n: workouts to handle.
    match progress.kind:
        case JobKind.SEND:
            return QCoreApplication.translate(
                "GarminCalendar", "Sending workout {step} of %n", "", total
            ).format(step=step)
        case JobKind.DELETE:
            return QCoreApplication.translate(
                "GarminCalendar", "Deleting workout {step} of %n", "", total
            ).format(step=step)
        case JobKind.RECREATE:
            return QCoreApplication.translate(
                "GarminCalendar", "Creating workout {step} of %n again", "", total
            ).format(step=step)
        case JobKind.LIST:
            return QCoreApplication.translate("GarminCalendar", "Reading the Garmin calendar...")


def starting_text() -> str:
    """Before the first progress arrives."""
    return QCoreApplication.translate("GarminCalendar", "Starting the Garmin task...")


# --- Outcomes ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CalendarSummary:
    """The window that tells how a job went: a title and lines, one per fact."""

    title: str
    lines: tuple[str, ...]


def _stop_lines() -> dict[StopReason, Callable[[], str]]:
    return {
        StopReason.CANCELLED: lambda: QCoreApplication.translate(
            "GarminCalendar", "You stopped the task; what was done is kept."
        ),
        StopReason.AUTHENTICATION_REQUIRED: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "Garmin asks you to connect again. Connect the account and repeat the command.",
        ),
        StopReason.RATE_LIMITED: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "Garmin is limiting the requests. Wait a few minutes and repeat the command.",
        ),
        StopReason.UNAVAILABLE: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "Garmin did not answer. Check the connection and repeat the command.",
        ),
        StopReason.UNSUPPORTED: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "This Garmin connection cannot manage workouts. Nothing was changed.",
        ),
        StopReason.BACKUP_FAILED: lambda: QCoreApplication.translate(
            "GarminCalendar",
            "A copy could not be saved on this computer, so nothing more was deleted.",
        ),
    }


_STOP_LINES = _stop_lines()


def _stop_line(stop: StopReason) -> str | None:
    """What a stop means, in plain words; `None` for a job that completed."""
    line = _STOP_LINES.get(stop)
    return None if line is None else line()


def failure_reason_text(reason: FailureReason) -> str:
    """Why one workout was left out, in plain words."""
    match reason:
        case FailureReason.REJECTED:
            return QCoreApplication.translate("GarminCalendar", "Garmin refused it")
        case FailureReason.TOO_COMPLEX:
            return QCoreApplication.translate(
                "GarminCalendar", "it has more steps than a watch can take"
            )
        case FailureReason.NOT_FUTURE:
            return QCoreApplication.translate("GarminCalendar", "its day has passed")
        case FailureReason.NO_COPY:
            return QCoreApplication.translate(
                "GarminCalendar", "it could not be copied first, so it was kept"
            )
        case FailureReason.EXPIRED:
            return QCoreApplication.translate(
                "GarminCalendar", "its day has passed, so it was not created again"
            )
        case FailureReason.NOT_IN_PLAN:
            return QCoreApplication.translate(
                "GarminCalendar", "the plan no longer has this session, so it was not created again"
            )


def _failure_lines(failures: Sequence[Failure]) -> list[str]:
    lines: list[str] = []
    for failure in failures[:_MAX_FAILURE_LINES]:
        reason = failure_reason_text(failure.reason)
        if failure.name:
            # {day}: the day; {name}: the workout; {reason}: why it was left out.
            line = QCoreApplication.translate("GarminCalendar", "{day}: {name} - {reason}").format(
                day=day_text(failure.day), name=failure.name, reason=reason
            )
        else:
            # {day}: the day; {reason}: why the session was left out.
            line = QCoreApplication.translate("GarminCalendar", "{day}: {reason}").format(
                day=day_text(failure.day), reason=reason
            )
        lines.append(line)
    hidden = len(failures) - _MAX_FAILURE_LINES
    if hidden > 0:
        lines.append(QCoreApplication.translate("GarminCalendar", "and %n more", "", hidden))
    return lines


def _count_line(count: int, text: Callable[[int], str]) -> list[str]:
    return [text(count)] if count else []


def _not_started() -> dict[CalendarProblem, Callable[[], tuple[str, str]]]:
    return {
        CalendarProblem.NOT_LINKED: lambda: (
            QCoreApplication.translate("GarminCalendar", "No Garmin account is connected."),
            QCoreApplication.translate(
                "GarminCalendar", "Connect an account from the Garmin menu."
            ),
        ),
        CalendarProblem.AUTH_REQUIRED: lambda: (
            QCoreApplication.translate("GarminCalendar", "Garmin asks you to connect again."),
            QCoreApplication.translate(
                "GarminCalendar", "Connect the account again and repeat the command."
            ),
        ),
        CalendarProblem.KEYCHAIN_UNAVAILABLE: lambda: (
            QCoreApplication.translate("GarminCalendar", "The system keychain cannot be used."),
            QCoreApplication.translate(
                "GarminCalendar", "Check that the keychain is unlocked and try again."
            ),
        ),
        CalendarProblem.RATE_LIMITED: lambda: (
            QCoreApplication.translate("GarminCalendar", "Garmin is limiting the requests."),
            QCoreApplication.translate("GarminCalendar", "Wait a few minutes and try again."),
        ),
        CalendarProblem.SERVICE_UNAVAILABLE: lambda: (
            QCoreApplication.translate("GarminCalendar", "Garmin did not answer."),
            QCoreApplication.translate("GarminCalendar", "Check the connection and try again."),
        ),
        CalendarProblem.PROFILE_UNAVAILABLE: lambda: (
            QCoreApplication.translate(
                "GarminCalendar", "The profile could not be opened for this task."
            ),
            QCoreApplication.translate(
                "GarminCalendar", "Check that its folder is available and try again."
            ),
        ),
        CalendarProblem.UNSUPPORTED: lambda: (
            QCoreApplication.translate(
                "GarminCalendar", "This Garmin connection cannot manage workouts."
            ),
            QCoreApplication.translate(
                "GarminCalendar", "Nothing was changed. Importing and syncing still work."
            ),
        ),
        CalendarProblem.UNEXPECTED: lambda: (
            QCoreApplication.translate("GarminCalendar", "The Garmin task failed unexpectedly."),
            QCoreApplication.translate(
                "GarminCalendar", "Try again; if it keeps failing, see the log."
            ),
        ),
    }


_NOT_STARTED = _not_started()


def not_started_text(problem: CalendarProblem) -> tuple[str, str]:
    """What happened and what to do, for a job that did not start."""
    return _NOT_STARTED[problem]()


def _title(result: Sent | Deleted | Restored) -> str:
    stop = result.outcome.stop
    incomplete = needs_attention(result)
    match result:
        case Sent():
            if stop is StopReason.CANCELLED:
                return QCoreApplication.translate("GarminCalendar", "Sending stopped")
            return (
                QCoreApplication.translate("GarminCalendar", "The plan was not fully sent")
                if incomplete
                else QCoreApplication.translate("GarminCalendar", "Plan sent to Garmin")
            )
        case Deleted():
            if stop is StopReason.CANCELLED:
                return QCoreApplication.translate("GarminCalendar", "Deletion stopped")
            return (
                QCoreApplication.translate("GarminCalendar", "Some workouts were not deleted")
                if incomplete
                else QCoreApplication.translate("GarminCalendar", "Workouts deleted")
            )
        case Restored():
            if stop is StopReason.CANCELLED:
                return QCoreApplication.translate("GarminCalendar", "Recreation stopped")
            return (
                QCoreApplication.translate("GarminCalendar", "Some workouts were not created again")
                if incomplete
                else QCoreApplication.translate("GarminCalendar", "Workouts created again")
            )


def _facts(result: Sent | Deleted | Restored) -> list[str]:
    outcome = result.outcome
    lines: list[str] = []
    match result:
        case Sent(sent_outcome):
            lines += _count_line(
                len(sent_outcome.sent),
                lambda n: QCoreApplication.translate(
                    "GarminCalendar", "Workouts sent to Garmin: %n", "", n
                ),
            )
            lines += _count_line(
                len(sent_outcome.already_there),
                lambda n: QCoreApplication.translate(
                    "GarminCalendar", "Already on the calendar: %n", "", n
                ),
            )
            if sent_outcome.deferred:
                # {limit}: workouts sent at a time; %n: sessions left for another send.
                lines.append(
                    QCoreApplication.translate(
                        "GarminCalendar",
                        "Sessions left for a later send: %n (at most {limit} are sent at a time)",
                        "",
                        sent_outcome.deferred,
                    ).format(limit=MAX_WORKOUTS_PER_SEND)
                )
        case Deleted(deleted_outcome):
            lines += _count_line(
                len(deleted_outcome.deleted),
                lambda n: QCoreApplication.translate(
                    "GarminCalendar",
                    "Workouts deleted from the Garmin calendar: %n. A copy of each is kept on this "
                    "computer.",
                    "",
                    n,
                ),
            )
        case Restored(restored_outcome):
            lines += _count_line(
                len(restored_outcome.restored),
                lambda n: QCoreApplication.translate(
                    "GarminCalendar", "Workouts created again: %n", "", n
                ),
            )
            lines += _count_line(
                len(restored_outcome.already_there),
                lambda n: QCoreApplication.translate(
                    "GarminCalendar", "Already on the calendar: %n", "", n
                ),
            )
    if outcome.failed:
        lines += _failure_lines(outcome.failed)
    return lines


def summary_of(result: CalendarResult) -> CalendarSummary | None:
    """The summary of a send, deletion or recreation, of a job that did not start, or of a list
    that is incomplete; `None` for a complete list (it is shown in its own window)."""
    if isinstance(result, Listed):
        line = list_problem_text(result)
        if line is None:
            return None
        title = QCoreApplication.translate(
            "GarminCalendar", "The Garmin calendar was not fully read"
        )
        return CalendarSummary(title, (line,))
    if isinstance(result, CalendarNotStarted):
        what, remedy = not_started_text(result.problem)
        title = QCoreApplication.translate("GarminCalendar", "The Garmin task did not start")
        return CalendarSummary(title, (what, remedy))
    lines = _facts(result)
    stop = _stop_line(result.outcome.stop)
    if stop is not None:
        lines.append(stop)
    if not lines:
        lines.append(_nothing_text(result))
    return CalendarSummary(_title(result), tuple(lines))


def _nothing_text(result: Sent | Deleted | Restored) -> str:
    match result:
        case Sent():
            return QCoreApplication.translate("GarminCalendar", "There was nothing to send.")
        case Deleted():
            return QCoreApplication.translate("GarminCalendar", "Nothing was deleted.")
        case Restored():
            return QCoreApplication.translate(
                "GarminCalendar", "There was nothing to create again."
            )


def headline_text(result: CalendarResult) -> str:
    """One sentence for the status bar."""
    match result:
        case Sent(outcome):
            count = len(outcome.sent) + len(outcome.already_there)
            return QCoreApplication.translate(
                "GarminCalendar", "Workouts on the Garmin calendar: %n", "", count
            )
        case Deleted(outcome):
            return QCoreApplication.translate(
                "GarminCalendar", "Workouts deleted from Garmin: %n", "", len(outcome.deleted)
            )
        case Restored(outcome):
            count = len(outcome.restored) + len(outcome.already_there)
            return QCoreApplication.translate(
                "GarminCalendar", "Workouts created again on Garmin: %n", "", count
            )
        case Listed(outcome):
            return QCoreApplication.translate(
                "GarminCalendar", "Workouts on the Garmin calendar: %n", "", len(outcome.workouts)
            )
        case CalendarNotStarted(problem):
            return not_started_text(problem)[0]


def list_problem_text(result: Listed) -> str | None:
    """Why the list may be incomplete, or `None` if it is complete."""
    stop = stop_of(result)
    return None if stop is None else _stop_line(stop)


def listed_workouts_text(items: Sequence[ScheduledWorkout]) -> str:
    """The workouts of a confirmation, one per line; the first few, then how many more."""
    lines = [workout_line(item) for item in items[:_MAX_LISTED_WORKOUTS]]
    hidden = len(items) - _MAX_LISTED_WORKOUTS
    if hidden > 0:
        lines.append(QCoreApplication.translate("GarminCalendar", "and %n more", "", hidden))
    return "\n".join(lines)


def all_workouts_text(items: Sequence[ScheduledWorkout]) -> str:
    """Every workout of a confirmation, one per line (the window's details)."""
    return "\n".join(workout_line(item) for item in items)
