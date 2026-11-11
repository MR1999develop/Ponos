# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The workout part of the "client": what the calendar adapter asks of `garminconnect`.

Separate from `client.GarminClient` so that an adapter built on a client without these methods
(or a library that drops them) still works for activities. Responses are untrusted JSON
(`object`) that `workout_json` reads at the boundary; the errors are the library's, not yet
translated. Every call has the library's timeout (15 s).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class GarminWorkoutClient(Protocol):
    """Workout library and calendar of an authenticated Garmin Connect session."""

    def upload_workout(self, payload: dict[str, object]) -> object:
        """Creates a workout in the library; the response should carry its `workoutId`."""
        ...

    def schedule_workout(self, workout_id: int, day_iso: str) -> object:
        """Puts a library workout on a day (`YYYY-MM-DD`); the response should carry the id of
        the schedule entry."""
        ...

    def scheduled_between(self, start_iso: str, end_iso: str) -> object:
        """The calendar entries between two days (`YYYY-MM-DD`, both included)."""
        ...

    def get_schedule(self, schedule_id: int) -> object:
        """One entry of the calendar, by the id of the schedule."""
        ...

    def get_workout(self, workout_id: int) -> object:
        """The definition of a workout of the library."""
        ...

    def unschedule_workout(self, schedule_id: int) -> None:
        """Takes an entry off the calendar, leaving the workout in the library."""
        ...

    def delete_workout(self, workout_id: int) -> None:
        """Deletes a workout from the library."""
        ...
