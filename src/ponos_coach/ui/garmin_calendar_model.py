# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation model of the Garmin calendar commands: testable without a window.

The decisions live here: which commands are available (and why not), which sessions a scope
covers, what a job asks for and how it ended. `garmin_calendar_text` chooses the texts, the views
only show them. The use cases and their outcomes are those of `planning.calendar_use_cases`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final, TypeGuard

from ponos_coach.planning.calendar_use_cases import (
    MAX_DELETIONS_PER_CALL,
    DeleteOutcome,
    Failure,
    FailureReason,
    ListOutcome,
    RestoreOutcome,
    SendOutcome,
    SendScope,
    SessionOfDay,
    StopReason,
    WeekOfPlan,
    WholePlan,
)
from ponos_coach.planning.domain import Plan, PlanHalted
from ponos_coach.planning.domain.workouts import ScheduledWorkout, planned_workouts
from ponos_coach.planning.workout_calendar import StoredBackup
from ponos_coach.ui.sync_model import DisabledReason, SyncProblem

LIST_AHEAD_DAYS: Final = 399  # the calendar list reaches this far from today (one request)
_DAYS_PER_WEEK: Final = 7
MAX_SELECTED_FOR_DELETION: Final = MAX_DELETIONS_PER_CALL


# --- What a job asks for ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SendRequest:
    """Send the sessions of `scope` (from `today` on) of `plan`."""

    plan: Plan
    scope: SendScope
    today: date


@dataclass(frozen=True, slots=True)
class ListRequest:
    """List the workouts on the calendar from `first` to `last`, and the copies waiting."""

    first: date
    last: date


@dataclass(frozen=True, slots=True)
class DeleteRequest:
    """Delete exactly these workouts (the user's confirmed choice)."""

    items: tuple[ScheduledWorkout, ...]
    today: date


@dataclass(frozen=True, slots=True)
class RecreateRequest:
    """Create again the deleted workouts waiting (those with the ids in `only`, if given) that the
    current `plan` (`None`: there is none) still has."""

    today: date
    plan: Plan | None
    only: frozenset[int] | None = None


type CalendarRequest = SendRequest | ListRequest | DeleteRequest | RecreateRequest


# --- What a job reports and returns ------------------------------------------------------------


class JobKind(Enum):
    """What a job does, for the progress text."""

    SEND = "send"
    LIST = "list"
    DELETE = "delete"
    RECREATE = "recreate"


@dataclass(frozen=True, slots=True)
class CalendarProgress:
    """`done` steps of `total` (`None`: not known, as when listing)."""

    kind: JobKind
    done: int
    total: int | None


class CalendarProblem(Enum):
    """Why a job did not even start."""

    NOT_LINKED = "not_linked"
    AUTH_REQUIRED = "auth_required"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    UNSUPPORTED = "unsupported"  # the Garmin connection cannot do workouts
    UNEXPECTED = "unexpected"


_PROBLEM_OF_SYNC: Final = {
    SyncProblem.NOT_LINKED: CalendarProblem.NOT_LINKED,
    SyncProblem.AUTH_REQUIRED: CalendarProblem.AUTH_REQUIRED,
    SyncProblem.KEYCHAIN_UNAVAILABLE: CalendarProblem.KEYCHAIN_UNAVAILABLE,
    SyncProblem.RATE_LIMITED: CalendarProblem.RATE_LIMITED,
    SyncProblem.SERVICE_UNAVAILABLE: CalendarProblem.SERVICE_UNAVAILABLE,
    SyncProblem.PROFILE_UNAVAILABLE: CalendarProblem.PROFILE_UNAVAILABLE,
    SyncProblem.UNEXPECTED: CalendarProblem.UNEXPECTED,
}


def calendar_problem_of(problem: SyncProblem) -> CalendarProblem:
    """The problem of a session that could not be had, as the calendar tells it."""
    return _PROBLEM_OF_SYNC[problem]


@dataclass(frozen=True, slots=True)
class CalendarNotStarted:
    """The job produced no outcome: `problem` says why."""

    problem: CalendarProblem


@dataclass(frozen=True, slots=True)
class Sent:
    """A send ended."""

    outcome: SendOutcome


@dataclass(frozen=True, slots=True)
class Listed:
    """A list ended: the workouts and the local copies waiting to be created again."""

    outcome: ListOutcome
    pending: tuple[StoredBackup, ...]


@dataclass(frozen=True, slots=True)
class Deleted:
    """A deletion ended."""

    outcome: DeleteOutcome


@dataclass(frozen=True, slots=True)
class Restored:
    """A recreation ended."""

    outcome: RestoreOutcome


type CalendarResult = CalendarNotStarted | Sent | Listed | Deleted | Restored


def is_calendar_result(value: object) -> TypeGuard[CalendarResult]:
    """True if `value` is the result of a calendar job."""
    return isinstance(value, CalendarNotStarted | Sent | Listed | Deleted | Restored)


def stop_of(result: CalendarResult) -> StopReason | None:
    """Why the job stopped early or for another cause; `None` if it did not even start."""
    if isinstance(result, CalendarNotStarted):
        return None
    return result.outcome.stop


def needs_reconnect(result: CalendarResult) -> bool:
    """Garmin asks for a new login."""
    if isinstance(result, CalendarNotStarted):
        return result.problem is CalendarProblem.AUTH_REQUIRED
    return result.outcome.stop is StopReason.AUTHENTICATION_REQUIRED


def is_unsupported(result: CalendarResult) -> bool:
    """The Garmin connection cannot do workouts."""
    if isinstance(result, CalendarNotStarted):
        return result.problem is CalendarProblem.UNSUPPORTED
    return result.outcome.stop is StopReason.UNSUPPORTED


def failures_of(result: CalendarResult) -> tuple[Failure, ...]:
    """The workouts a job left out, in the order it found them."""
    if isinstance(result, Sent | Deleted | Restored):
        return result.outcome.failed
    return ()


def failure_counts(failures: Sequence[Failure]) -> tuple[tuple[FailureReason, int], ...]:
    """The failures by cause, most frequent first (ties in enum order)."""
    counts: dict[FailureReason, int] = {}
    for failure in failures:
        counts[failure.reason] = counts.get(failure.reason, 0) + 1
    order = {reason: position for position, reason in enumerate(FailureReason)}
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], order[item[0]])))


def needs_attention(result: CalendarResult) -> bool:
    """The summary deserves a window: something was left out or the job did not complete.

    A job that did everything it was asked is told in the status bar and in the summary all the
    same (a send is a big step); this says only whether the summary is a warning.
    """
    if isinstance(result, CalendarNotStarted):
        return True
    stop = result.outcome.stop
    return bool(failures_of(result)) or stop not in (StopReason.COMPLETED, StopReason.CANCELLED)


def plan_is_halted(plan: Plan | None) -> bool:
    """True if the plan sends no session at all because it is stopped."""
    return plan is not None and any(isinstance(w, PlanHalted) for w in plan.warnings)


def unplanned_app_workouts(
    plan: Plan | None, workouts: Sequence[ScheduledWorkout], today: date
) -> tuple[ScheduledWorkout, ...]:
    """The app's own workouts on the calendar, from `today` on, that the current plan no longer
    has on that day (the plan changed or is stopped), at most one deletion's worth. The athlete's
    own workouts are never in it."""
    planned = planned_workouts(() if plan is None else plan.days)
    stale = (
        w
        for w in workouts
        if w.is_app_created and w.day >= today and (w.day, w.name) not in planned
    )
    return tuple(stale)[:MAX_SELECTED_FOR_DELETION]


# --- Scopes and the sessions they cover --------------------------------------------------------


class ScopeChoice(Enum):
    """What the user can send: the whole plan, the week of the selected day, or its session."""

    WHOLE_PLAN = "whole_plan"
    WEEK = "week"
    SESSION = "session"


def week_start(day: date) -> date:
    """The Monday of the week of `day`."""
    return day - timedelta(days=day.weekday())


def scope_of(choice: ScopeChoice, selected_day: date | None) -> SendScope | None:
    """The scope for a choice; `None` for week or session without a selected day."""
    if choice is ScopeChoice.WHOLE_PLAN:
        return WholePlan()
    if selected_day is None:
        return None
    if choice is ScopeChoice.WEEK:
        return WeekOfPlan(week_start(selected_day))
    return SessionOfDay(selected_day)


def sessions_in(plan: Plan, scope: SendScope, today: date) -> int:
    """How many sessions the scope covers from `today` on: those a send would try (before the
    cap of one send, which `deferred` reports)."""
    first = today
    last: date | None = None
    match scope:
        case WholePlan():
            pass
        case WeekOfPlan(start):
            first, last = max(start, today), start + timedelta(days=_DAYS_PER_WEEK - 1)
        case SessionOfDay(day):
            first, last = max(day, today), day
    return sum(
        1
        for item in plan.days
        if item.session is not None and item.day >= first and (last is None or item.day <= last)
    )


# --- Which commands can be given ---------------------------------------------------------------


class CalendarReason(Enum):
    """Why a command of the calendar is off; the view turns it into the tooltip."""

    NO_PROFILE = "no_profile"
    KEYCHAIN_UNAVAILABLE = "keychain_unavailable"
    CHECKING = "checking"
    BUSY = "busy"
    NOT_LINKED = "not_linked"
    NEEDS_RECONNECT = "needs_reconnect"
    UNSUPPORTED = "unsupported"
    NO_PLAN = "no_plan"
    NO_SESSIONS = "no_sessions"
    NO_DAY_SELECTED = "no_day_selected"
    NO_SESSION_ON_DAY = "no_session_on_day"
    NO_SESSIONS_THAT_WEEK = "no_sessions_that_week"
    DAY_PASSED = "day_passed"


_REASON_OF_LINK: Final = {
    DisabledReason.NO_PROFILE: CalendarReason.NO_PROFILE,
    DisabledReason.KEYCHAIN_UNAVAILABLE: CalendarReason.KEYCHAIN_UNAVAILABLE,
    DisabledReason.CHECKING: CalendarReason.CHECKING,
    DisabledReason.NOT_LINKED: CalendarReason.NOT_LINKED,
    DisabledReason.NEEDS_RECONNECT: CalendarReason.NEEDS_RECONNECT,
}


@dataclass(frozen=True, slots=True)
class CalendarAvailability:
    """A command that is available or not, with the reason."""

    reason: CalendarReason | None = None

    @property
    def enabled(self) -> bool:
        """True if the command can be given."""
        return self.reason is None


@dataclass(frozen=True, slots=True)
class CalendarInputs:
    """What the calendar commands depend on.

    `link` is why the account cannot be used now (`None`: it can), as the synchronisation commands
    tell it; `supported` is false once the Garmin connection has said it cannot do workouts.
    """

    link: DisabledReason | None
    supported: bool
    plan: Plan | None
    today: date
    selected_day: date | None


@dataclass(frozen=True, slots=True)
class CalendarCommands:
    """Availability of the calendar commands."""

    send_plan: CalendarAvailability
    send_week: CalendarAvailability
    send_session: CalendarAvailability
    browse: CalendarAvailability
    recreate: CalendarAvailability


def _connection_reason(inputs: CalendarInputs) -> CalendarReason | None:
    if inputs.link is not None:
        return _REASON_OF_LINK.get(inputs.link, CalendarReason.BUSY)
    return None if inputs.supported else CalendarReason.UNSUPPORTED


def _scope_reason(
    inputs: CalendarInputs, choice: ScopeChoice, empty: CalendarReason
) -> CalendarReason | None:
    connection = _connection_reason(inputs)
    if connection is not None:
        return connection
    plan = inputs.plan
    if plan is None:
        return CalendarReason.NO_PLAN
    if choice is not ScopeChoice.WHOLE_PLAN and inputs.selected_day is None:
        return CalendarReason.NO_DAY_SELECTED
    day = inputs.selected_day
    if choice is ScopeChoice.SESSION and day is not None and day < inputs.today:
        return CalendarReason.DAY_PASSED
    scope = scope_of(choice, inputs.selected_day)
    if scope is None or sessions_in(plan, scope, inputs.today) == 0:
        return empty
    return None


def commands_for(inputs: CalendarInputs) -> CalendarCommands:
    """Which calendar commands are available and, for the others, why not."""
    connection = _connection_reason(inputs)
    return CalendarCommands(
        send_plan=CalendarAvailability(
            _scope_reason(inputs, ScopeChoice.WHOLE_PLAN, CalendarReason.NO_SESSIONS)
        ),
        send_week=CalendarAvailability(
            _scope_reason(inputs, ScopeChoice.WEEK, CalendarReason.NO_SESSIONS_THAT_WEEK)
        ),
        send_session=CalendarAvailability(
            _scope_reason(inputs, ScopeChoice.SESSION, CalendarReason.NO_SESSION_ON_DAY)
        ),
        browse=CalendarAvailability(connection),
        recreate=CalendarAvailability(connection),
    )


# --- The list of the Garmin calendar -----------------------------------------------------------


def too_many_selected(selected: int) -> bool:
    """True if the selection is too big for one deletion (the view says so)."""
    return selected > MAX_SELECTED_FOR_DELETION
