# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A stateful fake of the Garmin workout service, at the level of `GarminWorkoutClient`.

It answers with the JSON shapes the adapter expects (`workout_json`) and keeps a library and a
calendar. Failures are injected per method with `fail`: the request raises the library's real
error and changes nothing. No network, no real data.
"""

from __future__ import annotations

import copy
from collections import defaultdict, deque
from datetime import date

from ponos_coach.garmin.workout_json import definition_text
from tests.garmin.scripted import not_found_error


class FakeWorkoutService:
    """`GarminWorkoutClient` on dictionaries."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.uploaded: list[dict[str, object]] = []
        self._next_id = 5000
        self._library: dict[int, dict[str, object]] = {}
        self._schedule: dict[int, tuple[int, str]] = {}  # schedule id -> (workout id, day)
        self._faults: defaultdict[str, deque[Exception]] = defaultdict(deque)

    def fail(self, method: str, error: Exception, times: int = 1) -> None:
        """The next `times` calls of `method` raise `error` and change nothing."""
        self._faults[method].extend([error] * times)

    def library_ids(self) -> set[int]:
        """Ids of the workouts in the library."""
        return set(self._library)

    def schedule_ids(self) -> set[int]:
        """Ids of the entries of the calendar."""
        return set(self._schedule)

    def add_foreign(self, name: str, day: date) -> tuple[int, int]:
        """A workout of the athlete on `day`: (workout id, schedule id)."""
        workout_id = self._store({"workoutName": name, "workoutSegments": []})
        return workout_id, self._put(workout_id, day.isoformat())

    # --- GarminWorkoutClient --------------------------------------------------------------------
    def upload_workout(self, payload: dict[str, object]) -> object:
        self._enter("upload_workout")
        self.uploaded.append(copy.deepcopy(payload))
        workout_id = self._store(payload)
        return {"workoutId": workout_id, "workoutName": payload["workoutName"], "ownerId": 7}

    def schedule_workout(self, workout_id: int, day_iso: str) -> object:
        self._enter("schedule_workout")
        if workout_id not in self._library:
            raise not_found_error()
        schedule_id = self._put(workout_id, day_iso)
        return {"workoutScheduleId": schedule_id, "calendarDate": day_iso, "workout": {}}

    def scheduled_between(self, start_iso: str, end_iso: str) -> object:
        self._enter("scheduled_between")
        return [
            self._entry(schedule_id)
            for schedule_id, (_, day) in self._schedule.items()
            if start_iso <= day <= end_iso
        ]

    def get_schedule(self, schedule_id: int) -> object:
        self._enter("get_schedule")
        if schedule_id not in self._schedule:
            raise not_found_error()
        return self._entry(schedule_id)

    def get_workout(self, workout_id: int) -> object:
        self._enter("get_workout")
        if workout_id not in self._library:
            raise not_found_error()
        return copy.deepcopy(self._library[workout_id])

    def unschedule_workout(self, schedule_id: int) -> None:
        self._enter("unschedule_workout")
        if schedule_id not in self._schedule:
            raise not_found_error()
        del self._schedule[schedule_id]

    def delete_workout(self, workout_id: int) -> None:
        self._enter("delete_workout")
        if workout_id not in self._library:
            raise not_found_error()
        del self._library[workout_id]

    # --- Internals ------------------------------------------------------------------------------
    def _enter(self, method: str) -> None:
        self.calls.append(method)
        if self._faults[method]:
            raise self._faults[method].popleft()

    def _store(self, payload: dict[str, object]) -> int:
        workout_id = self._next_id
        self._next_id += 1
        stored = copy.deepcopy(payload) | {"workoutId": workout_id, "ownerId": 7}
        self._library[workout_id] = stored
        return workout_id

    def _put(self, workout_id: int, day_iso: str) -> int:
        schedule_id = self._next_id
        self._next_id += 1
        self._schedule[schedule_id] = (workout_id, day_iso)
        return schedule_id

    def _entry(self, schedule_id: int) -> dict[str, object]:
        workout_id, day = self._schedule[schedule_id]
        name = self._library.get(workout_id, {}).get("workoutName", "")
        return {
            "workoutScheduleId": schedule_id,
            "calendarDate": day,
            "workout": {"workoutId": workout_id, "workoutName": name},
        }


def definition_of(service: FakeWorkoutService, workout_id: int) -> str:
    """The text the adapter keeps for a workout of the library."""
    return definition_text(service.get_workout(workout_id))
