# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Garmin calendar work, without Qt: sends, lists, deletes and creates again.

Runs on the thread `SyncController` starts. Like the synchronisation, it resumes the Garmin session
from the saved tokens and opens its own connection to the profile database (for the local copies
of what is deleted) and closes it at the end. Nothing here raises: the outcome says what happened.
In the logs only the type of the errors, never a workout or a plan.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from ponos_coach.activities.ports import Clock, GarminSession
from ponos_coach.planning.calendar_use_cases import (
    MAX_RESTORES_PER_CALL,
    MAX_WORKOUTS_PER_SEND,
    CalendarPorts,
    DeleteScheduledWorkouts,
    ListScheduledWorkouts,
    RecreateDeletedWorkouts,
    SendPlanToGarmin,
)
from ponos_coach.planning.domain.workouts import ScheduledWorkout, WorkoutSpec
from ponos_coach.planning.workout_calendar import (
    StoredBackup,
    WorkoutBackupStoreError,
    WorkoutCalendar,
)
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import ProfileStorage, ProfileStorageError
from ponos_coach.ui.diagnostics import exception_chain
from ponos_coach.ui.garmin_calendar_model import (
    CalendarNotStarted,
    CalendarProblem,
    CalendarProgress,
    CalendarRequest,
    CalendarResult,
    Deleted,
    DeleteRequest,
    JobKind,
    Listed,
    ListRequest,
    RecreateRequest,
    Restored,
    SendRequest,
    Sent,
    calendar_problem_of,
    sessions_in,
)
from ponos_coach.ui.sync_worker import SessionUnavailableError, SyncServices, SyncWorker

_LOG = logging.getLogger(__name__)
_US_PER_MS = 1000


@runtime_checkable
class _SessionWithCalendar(Protocol):
    """A session of an adapter that may also manage workouts (`calendar` is `None` if it cannot)."""

    @property
    def calendar(self) -> WorkoutCalendar | None: ...


def calendar_of(session: GarminSession) -> WorkoutCalendar | None:
    """The workout calendar of a session, or `None` if its adapter cannot do workouts."""
    return session.calendar if isinstance(session, _SessionWithCalendar) else None


class _ReportingCalendar:
    """Decorator of the port: tells the progress after each change it makes on Garmin."""

    def __init__(
        self,
        inner: WorkoutCalendar,
        kind: JobKind,
        total: int | None,
        report: Callable[[CalendarProgress], None],
    ) -> None:
        self._inner = inner
        self._kind = kind
        self._total = total
        self._report = report
        self._done = 0
        report(CalendarProgress(kind, 0, total))

    def _advanced(self) -> None:
        self._done += 1
        self._report(CalendarProgress(self._kind, self._done, self._total))

    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        created = self._inner.create_and_schedule(spec, day)
        self._advanced()
        return created

    def list_scheduled(self, first: date, last: date) -> Sequence[ScheduledWorkout]:
        return self._inner.list_scheduled(first, last)

    def export_definition(self, workout_id: int) -> str:
        return self._inner.export_definition(workout_id)

    def create_from_definition(self, payload: str, day: date) -> ScheduledWorkout:
        created = self._inner.create_from_definition(payload, day)
        self._advanced()
        return created

    def remove(self, item: ScheduledWorkout, *, delete_workout: bool) -> None:
        self._inner.remove(item, delete_workout=delete_workout)
        self._advanced()


class _WholeMillisecondClock:
    """The clock of the local copies: the store keeps whole milliseconds, so the instant is cut to
    them (a finer one would be refused)."""

    def __init__(self, inner: Clock) -> None:
        self._inner = inner

    def now(self) -> datetime:
        """The current instant in UTC, without the microseconds below the millisecond."""
        instant = self._inner.now()
        return instant.replace(microsecond=instant.microsecond // _US_PER_MS * _US_PER_MS)


class CalendarWorker:
    """The calendar jobs of the open profile; `run` returns a value and never raises."""

    def __init__(
        self, account: SyncWorker, services: SyncServices, storage: ProfileStorage
    ) -> None:
        self._account = account
        self._services = services
        self._storage = storage

    def run(
        self,
        request: CalendarRequest,
        profile_id: ProfileId,
        folder: Path,
        cancelled: threading.Event,
        report: Callable[[CalendarProgress], None],
    ) -> CalendarResult:
        """Do the job. If it cannot start, the result says why."""
        try:
            session = self._account.open_session(profile_id)
        except SessionUnavailableError as unavailable:
            return CalendarNotStarted(calendar_problem_of(unavailable.problem))
        except Exception as error:  # thread boundary: the error goes to the UI as "unexpected"
            _log_unexpected("session", error)
            return CalendarNotStarted(CalendarProblem.UNEXPECTED)
        calendar = calendar_of(session)
        if calendar is None:
            return CalendarNotStarted(CalendarProblem.UNSUPPORTED)
        try:
            return self._with_profile(request, calendar, folder, cancelled, report)
        except ProfileStorageError:
            return CalendarNotStarted(CalendarProblem.PROFILE_UNAVAILABLE)
        except Exception as error:  # thread boundary
            _log_unexpected("job", error)
            return CalendarNotStarted(CalendarProblem.UNEXPECTED)

    def _with_profile(
        self,
        request: CalendarRequest,
        calendar: WorkoutCalendar,
        folder: Path,
        cancelled: threading.Event,
        report: Callable[[CalendarProgress], None],
    ) -> CalendarResult:
        # Second connection to a profile already opened and verified by the data thread.
        opened = self._storage.open(folder, check_integrity=False)
        try:
            services = self._services
            backups = opened.workout_backups
            kind, total = _job_size(request, backups.pending)
            ports = CalendarPorts(
                calendar=_ReportingCalendar(calendar, kind, total, report),
                backups=backups,
                clock=_WholeMillisecondClock(services.clock),
                wait=cancelled.wait,
                rng=services.make_rng(),
                policy=services.policy,
            )
            return _dispatch(request, ports, cancelled.is_set)
        finally:
            _close_quietly(opened.close)


def _job_size(
    request: CalendarRequest, pending: Callable[[], Sequence[StoredBackup]]
) -> tuple[JobKind, int | None]:
    """What kind of job this is and how many steps it is expected to take (`None`: unknown)."""
    match request:
        case SendRequest(plan, scope, today):
            return JobKind.SEND, min(sessions_in(plan, scope, today), MAX_WORKOUTS_PER_SEND)
        case ListRequest():
            return JobKind.LIST, None
        case DeleteRequest(items):
            return JobKind.DELETE, len(items)
        case RecreateRequest(only=only):
            try:
                waiting = [b for b in pending() if only is None or b.backup_id in only]
            except WorkoutBackupStoreError:
                return JobKind.RECREATE, None
            return JobKind.RECREATE, min(len(waiting), MAX_RESTORES_PER_CALL)


def _dispatch(
    request: CalendarRequest, ports: CalendarPorts, is_cancelled: Callable[[], bool]
) -> CalendarResult:
    match request:
        case SendRequest(plan, scope, today):
            return Sent(SendPlanToGarmin(ports)(plan, scope, today, is_cancelled))
        case ListRequest(first, last):
            listed = ListScheduledWorkouts(ports)(first, last, is_cancelled)
            return Listed(listed, _pending_copies(ports))
        case DeleteRequest(items, today):
            return Deleted(DeleteScheduledWorkouts(ports)(items, today, is_cancelled))
        case RecreateRequest(today, only):
            return Restored(RecreateDeletedWorkouts(ports)(today, only, is_cancelled))


def _pending_copies(ports: CalendarPorts) -> tuple[StoredBackup, ...]:
    """The local copies waiting; if they cannot be read the list is shown without them."""
    try:
        return RecreateDeletedWorkouts(ports).pending()
    except WorkoutBackupStoreError as error:
        _log_unexpected("reading the local copies", error)
        return ()


def _close_quietly(close: Callable[[], None]) -> None:
    try:
        close()
    except Exception as error:  # closing does not change the job's outcome
        _log_unexpected("closing the profile connection", error)


def _log_unexpected(step: str, error: BaseException) -> None:
    """A single record per unexpected error, with the type only: the text may contain data."""
    _LOG.error("Garmin calendar: unexpected error in %s: %s", step, exception_chain(error))
