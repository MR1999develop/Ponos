# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the Garmin calendar: the workouts on it and the local copy of those deleted.

`WorkoutCalendar` is what the app asks of Garmin (create and schedule, list, export, remove). The
adapter may be incomplete (Garmin has no official API): an operation it cannot do raises
`CalendarUnsupportedError` and the rest keeps working. Errors are those of the Garmin source
(`activities.ports`): authentication required, rate limited, transient, definitive (rejected).
No message contains tokens, Garmin responses or health data.

`WorkoutBackupStore` keeps what the user deletes, in the profile, so that it can be created again.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol

from ponos_coach.activities.ports import DefinitiveSourceError
from ponos_coach.planning.domain.workouts import ScheduledWorkout, WorkoutSpec

MAX_BACKUP_PAYLOAD_CHARS: Final = 200_000  # a workout definition is a few KB; this is a ceiling
MAX_PENDING_BACKUPS: Final = 1_000
KEEP_RESTORED_BACKUPS_DAYS: Final = 30
MAX_LIST_DAYS: Final = 400  # the longest range of one `list_scheduled` call


class CalendarUnsupportedError(DefinitiveSourceError):
    """The adapter cannot do this operation (the library lacks it, or Garmin changed it)."""


class WorkoutBackupStoreError(Exception):
    """Root of the errors of the backup store (database unreadable, write failed, store full)."""


class WorkoutCalendar(Protocol):
    """The workout calendar of an authenticated Garmin session. Every call has a timeout.

    Pauses between calls and retries belong to the caller (`SendPlanToGarmin` and the other use
    cases); the adapter neither sleeps nor retries. Not thread-safe.
    """

    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        """Creates the workout in the library and puts it on `day`.

        If the scheduling fails after the creation, the adapter makes one attempt to delete the
        new workout, so that a failure does not leave clutter in the library.

        Raises:
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            DefinitiveSourceError (rejected), CalendarUnsupportedError.
        """
        ...

    def list_scheduled(self, first: date, last: date) -> Sequence[ScheduledWorkout]:
        """The workouts scheduled from `first` to `last` included, by day then name. Entries that
        are not workouts or cannot be read are left out.

        Raises:
            ValueError: `last` before `first`, or a longer range than `MAX_LIST_DAYS` allows.
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            DefinitiveSourceError, CalendarUnsupportedError.
        """
        ...

    def export_definition(self, workout_id: int) -> str:
        """The definition of a workout of the library as opaque JSON text (at most
        `MAX_BACKUP_PAYLOAD_CHARS`), to be given back to `create_from_definition`.

        Raises:
            DefinitiveSourceError: not found, too big or not interpretable.
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            CalendarUnsupportedError.
        """
        ...

    def create_from_definition(self, payload: str, day: date) -> ScheduledWorkout:
        """Creates a new workout from an exported definition (new ids) and puts it on `day`.

        Raises:
            DefinitiveSourceError: the definition is not acceptable.
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            CalendarUnsupportedError.
        """
        ...

    def remove(self, item: ScheduledWorkout, *, delete_workout: bool) -> None:
        """Takes `item` off the calendar; with `delete_workout` also deletes the workout from
        the library. A workout already gone counts as removed.

        Raises:
            AuthenticationRequiredError, RateLimitedError, TransientSourceError,
            DefinitiveSourceError, CalendarUnsupportedError.
        """
        ...


@dataclass(frozen=True, slots=True)
class NewBackup:
    """What is kept of a workout about to be deleted."""

    name: str
    day: date
    payload: str
    saved_at: datetime

    def __post_init__(self) -> None:
        if not 0 < len(self.payload) <= MAX_BACKUP_PAYLOAD_CHARS or not self.name:
            raise ValueError("a backup has a name and a bounded definition")


@dataclass(frozen=True, slots=True)
class StoredBackup:
    """A kept workout: `restored_at` is set once it has been created again."""

    backup_id: int
    name: str
    day: date
    payload: str
    saved_at: datetime
    restored_at: datetime | None = None


class WorkoutBackupStore(Protocol):
    """The local copy of the workouts the user deleted from the Garmin calendar."""

    def add(self, backup: NewBackup) -> StoredBackup:
        """Keeps a copy and forgets the restored ones older than `KEEP_RESTORED_BACKUPS_DAYS`.

        Raises:
            WorkoutBackupStoreError: write failed, or `MAX_PENDING_BACKUPS` copies are waiting.
        """
        ...

    def pending(self) -> Sequence[StoredBackup]:
        """The copies not yet restored, oldest first.

        Raises:
            WorkoutBackupStoreError: the database cannot be read.
        """
        ...

    def mark_restored(self, backup_id: int, when: datetime) -> None:
        """Records that the copy was created again; marking twice keeps the first time.

        Raises:
            WorkoutBackupStoreError: unknown id, or write failed.
        """
        ...
