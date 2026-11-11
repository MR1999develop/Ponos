# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the Garmin calendar: send the plan, list, delete and create again.

Rules shared by all of them (decisions of ADR 013):

- every request to Garmin comes after a pause with jitter (the same `SyncPolicy` as the
  synchronisation); a 429 or a transient error is retried with backoff up to the attempts of the
  policy and then the run stops: Garmin is not cooperating and more requests would not help;
- authentication required and cancellation stop the run; what was done before is kept and reported;
- a request that Garmin refuses (definitive error) fails that workout only;
- a send is idempotent: what is already on the calendar with the same name on the same day is not
  created again, and after a transient error the workout is looked up before it is created again;
- nothing is deleted from Garmin before a local copy of it is saved (the copy is dropped again if
  Garmin then refuses the deletion), and nothing is deleted that was not given to
  `DeleteScheduledWorkouts`;
- the workouts of the app are created again only if the current plan still has that session.

Health-related data (the sessions) never goes in messages.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Final

from ponos_coach.activities.domain import (
    DEFAULT_POLICY,
    RandomSource,
    SyncPolicy,
    pause_between_requests_s,
    retry_wait_s,
)
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    Clock,
    DefinitiveSourceError,
    RateLimitedError,
    TransientSourceError,
)
from ponos_coach.activities.sync import InterruptibleWait
from ponos_coach.planning.domain import Plan, PlanDay
from ponos_coach.planning.domain.workouts import (
    WORKOUT_PREFIX,
    ScheduledWorkout,
    WorkoutSpec,
    planned_workouts,
    workout_from_session,
)
from ponos_coach.planning.workout_calendar import (
    MAX_LIST_DAYS,
    CalendarUnsupportedError,
    NewBackup,
    StoredBackup,
    WorkoutBackupStore,
    WorkoutBackupStoreError,
    WorkoutCalendar,
)

MAX_WORKOUTS_PER_SEND: Final = 60
MAX_DELETIONS_PER_CALL: Final = 100
MAX_RESTORES_PER_CALL: Final = 60


def _never_cancelled() -> bool:
    return False


# --- What the use cases are given and what they report ------------------------------------------
@dataclass(frozen=True, slots=True)
class CalendarPorts:
    """Everything the use cases receive from the Composition Root. `rng` gives the jitter and
    `wait(seconds)` returns True if cancellation was requested during the wait."""

    calendar: WorkoutCalendar
    backups: WorkoutBackupStore
    clock: Clock
    wait: InterruptibleWait
    rng: RandomSource
    policy: SyncPolicy = DEFAULT_POLICY


class StopReason(Enum):
    """Why a run ended. Everything done before the stop is kept and reported."""

    COMPLETED = "completed"
    CANCELLED = "cancelled"
    AUTHENTICATION_REQUIRED = "authentication_required"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    BACKUP_FAILED = "backup_failed"


class FailureReason(Enum):
    """Why one workout was left out while the run went on."""

    REJECTED = "rejected"  # Garmin refused the request
    TOO_COMPLEX = "too_complex"  # more steps than a watch takes
    NOT_FUTURE = "not_future"  # only workouts of today or later are touched
    NO_COPY = "no_copy"  # its definition could not be read, so it was not deleted
    EXPIRED = "expired"  # its day has passed: not created again
    NOT_IN_PLAN = "not_in_plan"  # the plan no longer has this session: not created again


@dataclass(frozen=True, slots=True)
class Failure:
    """A workout left out: its day and name (the name is the app's own text or the athlete's)."""

    day: date
    name: str
    reason: FailureReason


@dataclass(frozen=True, slots=True)
class WholePlan:
    """Scope: every session of the plan from today on."""


@dataclass(frozen=True, slots=True)
class WeekOfPlan:
    """Scope: the sessions of the week (Monday to Sunday) that starts on `week_start`."""

    week_start: date


@dataclass(frozen=True, slots=True)
class SessionOfDay:
    """Scope: the session of one day."""

    day: date


type SendScope = WholePlan | WeekOfPlan | SessionOfDay


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """What a send did. `deferred` is the number of sessions not yet on the calendar beyond
    `MAX_WORKOUTS_PER_SEND`, left for another send (the earliest days go first; those already
    there do not count, so each send moves on)."""

    sent: tuple[ScheduledWorkout, ...]
    already_there: tuple[ScheduledWorkout, ...]
    failed: tuple[Failure, ...]
    deferred: int
    stop: StopReason


@dataclass(frozen=True, slots=True)
class ListOutcome:
    """The workouts found on the calendar (all of them if `stop` is `COMPLETED`)."""

    workouts: tuple[ScheduledWorkout, ...]
    stop: StopReason


@dataclass(frozen=True, slots=True)
class DeleteOutcome:
    """What a deletion did: the workouts taken off the calendar, each with a local copy."""

    deleted: tuple[ScheduledWorkout, ...]
    failed: tuple[Failure, ...]
    stop: StopReason


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    """What creating the deleted workouts again did."""

    restored: tuple[ScheduledWorkout, ...]
    already_there: tuple[ScheduledWorkout, ...]
    failed: tuple[Failure, ...]
    stop: StopReason


# --- Requests: pause, backoff, cancellation -----------------------------------------------------
class _StopError(Exception):
    """Ends the run; carries why."""

    def __init__(self, reason: StopReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class _Requester:
    """Makes the requests of one run: a pause before each, backoff on 429 and transient errors."""

    def __init__(self, ports: CalendarPorts, is_cancelled: Callable[[], bool]) -> None:
        self._ports = ports
        self._is_cancelled = is_cancelled
        self._requested_before = False

    def call[T](self, request: Callable[[], T], recover: Callable[[], T | None] | None = None) -> T:
        """Runs `request`. After a transient error (the answer may have been lost, the request
        may have worked) `recover` looks for what the request would have made, before trying
        again; if it finds it, that is the result.

        Raises:
            _StopError: cancelled, authentication required, unsupported, or attempts exhausted.
        Errors that are definitive for the request pass to the caller.
        """
        self._begin_request()
        failures = 0
        while True:
            self._check_cancelled()
            try:
                return request()
            except (AuthenticationRequiredError, CalendarUnsupportedError) as error:
                reason = (
                    StopReason.UNSUPPORTED
                    if isinstance(error, CalendarUnsupportedError)
                    else StopReason.AUTHENTICATION_REQUIRED
                )
                raise _StopError(reason) from error
            except RateLimitedError as error:
                failures += 1
                self._back_off(failures, StopReason.RATE_LIMITED, error.retry_after_s)
            except TransientSourceError:
                failures += 1
                self._back_off(failures, StopReason.UNAVAILABLE, None)
                found = None if recover is None else self.call(recover)
                if found is not None:
                    return found

    def _begin_request(self) -> None:
        self._check_cancelled()
        if self._requested_before:
            self._sleep(pause_between_requests_s(self._ports.policy, self._ports.rng))
        self._requested_before = True

    def _back_off(self, failures: int, reason: StopReason, suggested_s: float | None) -> None:
        wait_s = retry_wait_s(self._ports.policy, failures, self._ports.rng, suggested_s)
        if wait_s is None:
            raise _StopError(reason)
        self._sleep(wait_s)

    def _sleep(self, seconds: float) -> None:
        if self._ports.wait(seconds):
            raise _StopError(StopReason.CANCELLED)

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise _StopError(StopReason.CANCELLED)


def _find_on(
    calendar: WorkoutCalendar, name: str, day: date
) -> Callable[[], ScheduledWorkout | None]:
    """A lookup of the workout with this name on this day, for `_Requester.call(recover=...)`."""

    def find() -> ScheduledWorkout | None:
        return next((w for w in calendar.list_scheduled(day, day) if w.name == name), None)

    return find


def _windows(first: date, last: date) -> list[tuple[date, date]]:
    """`first`..`last` cut in ranges the port accepts."""
    out: list[tuple[date, date]] = []
    start = first
    while start <= last:
        end = min(last, start + timedelta(days=MAX_LIST_DAYS - 1))
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def _list_range(
    requester: _Requester, calendar: WorkoutCalendar, first: date, last: date
) -> tuple[ScheduledWorkout, ...]:
    found: list[ScheduledWorkout] = []
    for start, end in _windows(first, last):

        def list_window(start: date = start, end: date = end) -> tuple[ScheduledWorkout, ...]:
            return tuple(calendar.list_scheduled(start, end))

        found.extend(requester.call(list_window))
    return tuple(found)


# --- Use cases ----------------------------------------------------------------------------------
class ListScheduledWorkouts:
    """The workouts on the Garmin calendar from `first` to `last`, the athlete's and the app's."""

    def __init__(self, ports: CalendarPorts) -> None:
        self._ports = ports

    def __call__(
        self, first: date, last: date, is_cancelled: Callable[[], bool] = _never_cancelled
    ) -> ListOutcome:
        """Never raises for Garmin's errors: `stop` says why the list may be incomplete.

        Raises:
            ValueError: `last` before `first`.
        """
        if last < first:
            raise ValueError("the range is inverted")
        requester = _Requester(self._ports, is_cancelled)
        try:
            found = _list_range(requester, self._ports.calendar, first, last)
        except _StopError as stop:
            return ListOutcome((), stop.reason)
        except DefinitiveSourceError:
            return ListOutcome((), StopReason.UNAVAILABLE)
        return ListOutcome(found, StopReason.COMPLETED)


def _sessions_in_scope(plan: Plan, scope: SendScope, today: date) -> tuple[PlanDay, ...]:
    """The days with a session that the scope covers, from `today` on, earliest first."""

    def covered(day: date) -> bool:
        match scope:
            case WholePlan():
                return True
            case WeekOfPlan(week_start):
                return week_start <= day < week_start + timedelta(days=7)
            case SessionOfDay(only):
                return day == only

    days = (d for d in plan.days if d.session is not None and d.day >= today and covered(d.day))
    return tuple(sorted(days, key=lambda d: d.day))


class SendPlanToGarmin:
    """Sends the sessions of the plan to the Garmin calendar as structured workouts."""

    def __init__(self, ports: CalendarPorts) -> None:
        self._ports = ports

    def __call__(
        self,
        plan: Plan,
        scope: SendScope,
        today: date,
        is_cancelled: Callable[[], bool] = _never_cancelled,
    ) -> SendOutcome:
        """Creates and schedules a workout for each session of the scope from `today` on that is
        not on the calendar yet, at most `MAX_WORKOUTS_PER_SEND` (the earliest). A workout with
        the same name on the same day is left as it is and does not count towards the cap.
        Never raises for Garmin's errors: `stop` and `failed` say what happened."""
        chosen = _sessions_in_scope(plan, scope, today)
        run = _SendRun(self._ports, _Requester(self._ports, is_cancelled))
        stop = run.send_all(chosen)
        return SendOutcome(
            tuple(run.sent), tuple(run.already_there), tuple(run.failed), run.deferred, stop
        )


class _SendRun:
    def __init__(self, ports: CalendarPorts, requester: _Requester) -> None:
        self._calendar = ports.calendar
        self._requester = requester
        self.sent: list[ScheduledWorkout] = []
        self.already_there: list[ScheduledWorkout] = []
        self.failed: list[Failure] = []
        self.deferred = 0

    def send_all(self, days: Sequence[PlanDay]) -> StopReason:
        """Sends the days that are not on the calendar yet, up to the cap of one send."""
        self.deferred = max(0, len(days) - MAX_WORKOUTS_PER_SEND)  # until the calendar is read
        specs = self._specs_of(days)
        if not specs:
            return StopReason.COMPLETED
        try:
            existing = _list_range(self._requester, self._calendar, specs[0][0], specs[-1][0])
            missing = self._take_present(specs, existing)
            self.deferred = max(0, len(missing) - MAX_WORKOUTS_PER_SEND)
            for day, spec in missing[:MAX_WORKOUTS_PER_SEND]:
                self._send(day, spec)
        except _StopError as stop:
            return stop.reason
        except DefinitiveSourceError:  # the first look at the calendar was refused
            return StopReason.UNAVAILABLE
        return StopReason.COMPLETED

    def _specs_of(self, days: Sequence[PlanDay]) -> list[tuple[date, WorkoutSpec]]:
        """The workout of each day's session; one too long for a watch is reported as failed."""
        specs: list[tuple[date, WorkoutSpec]] = []
        for plan_day in days:
            if plan_day.session is None:
                continue
            try:
                specs.append((plan_day.day, workout_from_session(plan_day.session)))
            except ValueError:
                self.failed.append(Failure(plan_day.day, "", FailureReason.TOO_COMPLEX))
        return specs

    def _take_present(
        self, specs: Sequence[tuple[date, WorkoutSpec]], existing: Collection[ScheduledWorkout]
    ) -> list[tuple[date, WorkoutSpec]]:
        """Records what is already on the calendar; returns what still has to be created."""
        missing: list[tuple[date, WorkoutSpec]] = []
        for day, spec in specs:
            already = next((w for w in existing if w.name == spec.name and w.day == day), None)
            if already is None:
                missing.append((day, spec))
            else:
                self.already_there.append(already)
        return missing

    def _send(self, day: date, spec: WorkoutSpec) -> None:
        calendar = self._calendar
        try:
            created = self._requester.call(
                lambda: calendar.create_and_schedule(spec, day),
                _find_on(calendar, spec.name, day),
            )
        except DefinitiveSourceError:
            self.failed.append(Failure(day, spec.name, FailureReason.REJECTED))
            return
        self.sent.append(created)


class DeleteScheduledWorkouts:
    """Takes workouts off the Garmin calendar, after saving a local copy of each."""

    def __init__(self, ports: CalendarPorts) -> None:
        self._ports = ports

    def __call__(
        self,
        items: Sequence[ScheduledWorkout],
        today: date,
        is_cancelled: Callable[[], bool] = _never_cancelled,
    ) -> DeleteOutcome:
        """Deletes exactly `items` (the user's confirmed choice), each one only if it is today or
        later and its definition could be copied first. A workout the app created is also
        deleted from the library; one of the athlete's is only taken off the calendar.

        Raises:
            ValueError: more than `MAX_DELETIONS_PER_CALL` items.
        """
        if len(items) > MAX_DELETIONS_PER_CALL:
            raise ValueError("too many workouts in one deletion")
        requester = _Requester(self._ports, is_cancelled)
        deleted: list[ScheduledWorkout] = []
        failed: list[Failure] = []
        stop = StopReason.COMPLETED
        for item in items:
            try:
                outcome = self._delete_one(requester, item, today)
            except _StopError as stopped:
                stop = stopped.reason
                break
            if outcome is None:
                deleted.append(item)
            else:
                failed.append(Failure(item.day, item.name, outcome))
        return DeleteOutcome(tuple(deleted), tuple(failed), stop)

    def _delete_one(
        self, requester: _Requester, item: ScheduledWorkout, today: date
    ) -> FailureReason | None:
        """None when deleted, otherwise why not. Stops the run if the copy cannot be saved."""
        if item.day < today:
            return FailureReason.NOT_FUTURE
        calendar = self._ports.calendar
        try:
            payload = requester.call(lambda: calendar.export_definition(item.workout_id))
        except DefinitiveSourceError:
            return FailureReason.NO_COPY
        try:
            saved = self._ports.backups.add(
                NewBackup(item.name, item.day, payload, self._ports.clock.now())
            )
        except (WorkoutBackupStoreError, ValueError) as error:
            raise _StopError(StopReason.BACKUP_FAILED) from error
        try:
            requester.call(lambda: calendar.remove(item, delete_workout=item.is_app_created))
        except DefinitiveSourceError:
            # Garmin refused (the entry changed, or it said no): the workout is not deleted, so
            # its copy must not wait to be "created again" as a duplicate.
            self._drop(saved)
            return FailureReason.REJECTED
        return None

    def _drop(self, saved: StoredBackup) -> None:
        # If this fails the copy stays; creating it again is guarded by the plan and the calendar.
        with contextlib.suppress(WorkoutBackupStoreError):
            self._ports.backups.discard(saved.backup_id)


class RecreateDeletedWorkouts:
    """Creates again the workouts deleted before, from their local copies."""

    def __init__(self, ports: CalendarPorts) -> None:
        self._ports = ports

    def pending(self) -> tuple[StoredBackup, ...]:
        """The copies that can be created again, oldest first.

        Raises:
            WorkoutBackupStoreError: the database cannot be read.
        """
        return tuple(self._ports.backups.pending())

    def __call__(
        self,
        today: date,
        plan: Plan | None,
        only: Collection[int] | None = None,
        is_cancelled: Callable[[], bool] = _never_cancelled,
    ) -> RestoreOutcome:
        """Creates again the pending copies (or those with the ids in `only`), at most
        `MAX_RESTORES_PER_CALL`. A copy of the app's own workout is created only if `plan` (the
        current one, `None` if there is none) has that session on that day: otherwise it is left
        pending and reported as not in the plan (a halted or changed plan sends nothing back). A
        copy whose day has passed is left pending and reported as expired; one whose workout is
        already on its day (same name) counts as done. The athlete's own workouts do not depend
        on the plan."""
        try:
            chosen = [
                b for b in self._ports.backups.pending() if only is None or b.backup_id in only
            ]
        except WorkoutBackupStoreError:
            return RestoreOutcome((), (), (), StopReason.BACKUP_FAILED)
        planned = planned_workouts(() if plan is None else plan.days)
        requester = _Requester(self._ports, is_cancelled)
        restored: list[ScheduledWorkout] = []
        already: list[ScheduledWorkout] = []
        failed: list[Failure] = []
        stop = StopReason.COMPLETED
        for backup in chosen[:MAX_RESTORES_PER_CALL]:
            if backup.name.startswith(WORKOUT_PREFIX) and (backup.day, backup.name) not in planned:
                failed.append(Failure(backup.day, backup.name, FailureReason.NOT_IN_PLAN))
                continue
            try:
                self._restore_one(requester, backup, today, restored, already, failed)
            except _StopError as stopped:
                stop = stopped.reason
                break
            except WorkoutBackupStoreError:
                stop = StopReason.BACKUP_FAILED
                break
        return RestoreOutcome(tuple(restored), tuple(already), tuple(failed), stop)

    def _restore_one(
        self,
        requester: _Requester,
        backup: StoredBackup,
        today: date,
        restored: list[ScheduledWorkout],
        already: list[ScheduledWorkout],
        failed: list[Failure],
    ) -> None:
        if backup.day < today:
            failed.append(Failure(backup.day, backup.name, FailureReason.EXPIRED))
            return
        calendar = self._ports.calendar
        find = _find_on(calendar, backup.name, backup.day)
        try:
            present = requester.call(find)
            if present is not None:
                already.append(present)
            else:
                present = requester.call(
                    lambda: calendar.create_from_definition(backup.payload, backup.day), find
                )
                restored.append(present)
        except DefinitiveSourceError:
            failed.append(Failure(backup.day, backup.name, FailureReason.REJECTED))
            return
        self._ports.backups.mark_restored(backup.backup_id, self._ports.clock.now())
