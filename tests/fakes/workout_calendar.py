# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""In-memory fakes of the workout calendar and of the local copy of deleted workouts.

`InMemoryWorkoutCalendar` keeps its calendar in dictionaries; its definitions are small JSON texts.
Failures are injected per method with `fail(method, error, times)`: the call raises and changes
nothing, like a request that Garmin refused. `calls` records the method names, for the tests that
count requests. No real data.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Sequence
from datetime import date, datetime, timedelta

from ponos_coach.activities.ports import DefinitiveSourceError
from ponos_coach.planning.domain.workouts import ScheduledWorkout, WorkoutSpec
from ponos_coach.planning.workout_calendar import (
    KEEP_RESTORED_BACKUPS_DAYS,
    MAX_LIST_DAYS,
    MAX_PENDING_BACKUPS,
    NewBackup,
    StoredBackup,
    WorkoutBackupStoreError,
)


class InMemoryWorkoutCalendar:
    """`WorkoutCalendar` on dictionaries."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._next_id = 1000
        self._definitions: dict[int, str] = {}  # workout id -> JSON text
        self._entries: dict[int, ScheduledWorkout] = {}  # schedule id -> entry
        self._faults: defaultdict[str, deque[Exception]] = defaultdict(deque)

    # --- Scaffolding ----------------------------------------------------------------------------
    def fail(self, method: str, error: Exception, times: int = 1) -> None:
        """The next `times` calls of `method` raise `error` and change nothing."""
        self._faults[method].extend([error] * times)

    def add_foreign(self, name: str, day: date) -> ScheduledWorkout:
        """A workout of the athlete (not made by the app) on `day`."""
        return self._add({"workoutName": name, "steps": 1}, day)

    def scheduled(self) -> list[ScheduledWorkout]:
        """Everything on the calendar, by day then name."""
        return sorted(self._entries.values(), key=lambda w: (w.day, w.name))

    def library_ids(self) -> set[int]:
        """Ids of the workouts in the library."""
        return set(self._definitions)

    # --- WorkoutCalendar ------------------------------------------------------------------------
    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        self._enter("create_and_schedule")
        return self._add({"workoutName": spec.name, "steps": len(spec.steps)}, day)

    def list_scheduled(self, first: date, last: date) -> Sequence[ScheduledWorkout]:
        if last < first or last - first >= timedelta(days=MAX_LIST_DAYS):
            raise ValueError("invalid range")
        self._enter("list_scheduled")
        return [w for w in self.scheduled() if first <= w.day <= last]

    def export_definition(self, workout_id: int) -> str:
        self._enter("export_definition")
        if workout_id not in self._definitions:
            raise DefinitiveSourceError("not found")
        return self._definitions[workout_id]

    def create_from_definition(self, payload: str, day: date) -> ScheduledWorkout:
        self._enter("create_from_definition")
        return self._add(json.loads(payload), day)

    def remove(self, item: ScheduledWorkout, *, delete_workout: bool) -> None:
        self._enter("remove")
        if self._entries.get(item.schedule_id) == item:
            del self._entries[item.schedule_id]
        elif item.schedule_id in self._entries:
            raise DefinitiveSourceError("the calendar entry is no longer the one chosen")
        if delete_workout:
            self._definitions.pop(item.workout_id, None)

    # --- Internals ------------------------------------------------------------------------------
    def _enter(self, method: str) -> None:
        self.calls.append(method)
        if self._faults[method]:
            raise self._faults[method].popleft()

    def _add(self, definition: dict[str, object], day: date) -> ScheduledWorkout:
        workout_id, schedule_id = self._next_id, self._next_id + 1
        self._next_id += 2
        self._definitions[workout_id] = json.dumps(definition, sort_keys=True)
        entry = ScheduledWorkout(schedule_id, workout_id, str(definition["workoutName"]), day)
        self._entries[schedule_id] = entry
        return entry


class InMemoryWorkoutBackupStore:
    """`WorkoutBackupStore` in a list; same rules as the SQLite one."""

    def __init__(self) -> None:
        self._rows: list[StoredBackup] = []
        self._next_id = 1
        self.fail_on_add: Exception | None = None

    def add(self, backup: NewBackup) -> StoredBackup:
        if self.fail_on_add is not None:
            raise self.fail_on_add
        cutoff = backup.saved_at - timedelta(days=KEEP_RESTORED_BACKUPS_DAYS)
        self._rows = [r for r in self._rows if r.restored_at is None or r.restored_at >= cutoff]
        if len(self.pending()) >= MAX_PENDING_BACKUPS:
            raise WorkoutBackupStoreError("too many copies are waiting")
        row = StoredBackup(self._next_id, backup.name, backup.day, backup.payload, backup.saved_at)
        self._next_id += 1
        self._rows.append(row)
        return row

    def pending(self) -> Sequence[StoredBackup]:
        waiting = [r for r in self._rows if r.restored_at is None]
        return sorted(waiting, key=lambda r: (r.saved_at, r.backup_id))

    def mark_restored(self, backup_id: int, when: datetime) -> None:
        for index, row in enumerate(self._rows):
            if row.backup_id == backup_id:
                if row.restored_at is None:
                    self._rows[index] = StoredBackup(
                        row.backup_id,
                        row.name,
                        row.day,
                        row.payload,
                        row.saved_at,
                        max(when, row.saved_at),
                    )
                return
        raise WorkoutBackupStoreError("unknown copy")
