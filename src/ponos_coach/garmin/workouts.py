# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`WorkoutCalendar` on Garmin Connect: create, schedule, list, export and remove workouts.

Thin adapter on top of the workout "client" (`workout_client.GarminWorkoutClient`): it calls,
translates errors into the types of the Garmin source, reads the responses at the boundary
(`workout_json`) and keeps its promises to the port: a failed scheduling does not leave a new
workout in the library; a removal first checks that the entry is still the one the user chose.
Pauses and retries belong to the caller; the client does not retry.

Known limit: `garminconnect` reads a whole response before returning it, so the size caps here
apply after the read.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import date, timedelta

from ponos_coach.activities.ports import ActivitySourceError, DefinitiveSourceError
from ponos_coach.garmin.errors import is_not_found, translate_source_error
from ponos_coach.garmin.workout_client import GarminWorkoutClient
from ponos_coach.garmin.workout_json import (
    UnexpectedWorkoutResponseError,
    definition_payload,
    definition_text,
    entry_matches,
    parse_entries,
    parse_schedule_id,
    parse_workout_id,
    workout_payload,
)
from ponos_coach.planning.domain.workouts import ScheduledWorkout, WorkoutSpec
from ponos_coach.planning.workout_calendar import MAX_LIST_DAYS, CalendarUnsupportedError

_LOGGER = logging.getLogger(__name__)


class _GoneError(DefinitiveSourceError):
    """Garmin says the entry or workout is not there (404)."""


def _call[T](request: Callable[[], T]) -> T:
    """Runs one request of the client with its errors in the types of the Garmin source."""
    try:
        return request()
    except AttributeError as error:  # the library no longer has the method
        raise CalendarUnsupportedError("operation not available in this library") from error
    except Exception as error:
        if is_not_found(error):
            raise _GoneError("not found") from error
        raise translate_source_error(error) from error


def _read[A, R](value: A, parse: Callable[[A], R]) -> R:
    """`parse(value)` with a response that does not fit as a definitive error."""
    try:
        return parse(value)
    except UnexpectedWorkoutResponseError as error:
        raise DefinitiveSourceError("response not interpretable") from error


class GarminWorkoutCalendar:
    """The workout calendar of an already authenticated session. Not thread-safe."""

    def __init__(self, client: GarminWorkoutClient) -> None:
        self._client = client

    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        """See `WorkoutCalendar.create_and_schedule`."""
        payload = workout_payload(spec)
        client = self._client
        workout_id = _read(_call(lambda: client.upload_workout(payload)), parse_workout_id)
        schedule_id = self._schedule_or_discard(workout_id, day)
        return ScheduledWorkout(schedule_id, workout_id, spec.name, day)

    def list_scheduled(self, first: date, last: date) -> Sequence[ScheduledWorkout]:
        """See `WorkoutCalendar.list_scheduled`."""
        if last < first or last - first >= timedelta(days=MAX_LIST_DAYS):
            raise ValueError("invalid range")
        client = self._client
        response = _call(lambda: client.scheduled_between(first.isoformat(), last.isoformat()))
        found, skipped = _read(response, parse_entries)
        if skipped:
            _LOGGER.warning("calendar entries left out", extra={"skipped": skipped})
        return sorted((w for w in found if first <= w.day <= last), key=lambda w: (w.day, w.name))

    def export_definition(self, workout_id: int) -> str:
        """See `WorkoutCalendar.export_definition`."""
        client = self._client
        return _read(_call(lambda: client.get_workout(workout_id)), definition_text)

    def create_from_definition(self, payload: str, day: date) -> ScheduledWorkout:
        """See `WorkoutCalendar.create_from_definition`."""
        body = _read(payload, definition_payload)
        client = self._client
        workout_id = _read(_call(lambda: client.upload_workout(body)), parse_workout_id)
        schedule_id = self._schedule_or_discard(workout_id, day)
        return ScheduledWorkout(schedule_id, workout_id, str(body["workoutName"]), day)

    def remove(self, item: ScheduledWorkout, *, delete_workout: bool) -> None:
        """See `WorkoutCalendar.remove`.

        First it reads the entry back: if it no longer is the workout and the day the user chose
        (the calendar changed since the list) nothing is removed.
        """
        client = self._client
        if self._entry_is_still(item):
            _call(lambda: client.unschedule_workout(item.schedule_id))
        if delete_workout:
            try:
                _call(lambda: client.delete_workout(item.workout_id))
            except _GoneError:
                return  # already gone: the aim is reached

    def _entry_is_still(self, item: ScheduledWorkout) -> bool:
        """True if the entry is there and is `item`; False if it is already gone."""
        client = self._client
        try:
            current = _call(lambda: client.get_schedule(item.schedule_id))
        except _GoneError:
            return False
        if not entry_matches(current, item):
            raise DefinitiveSourceError("the calendar entry is no longer the one chosen")
        return True

    def _schedule_or_discard(self, workout_id: int, day: date) -> int:
        """Schedules a new workout; if that fails the workout is deleted (one attempt)."""
        client = self._client
        try:
            response = _call(lambda: client.schedule_workout(workout_id, day.isoformat()))
            return _read(response, parse_schedule_id)
        except ActivitySourceError:
            try:
                _call(lambda: client.delete_workout(workout_id))
            except ActivitySourceError:
                _LOGGER.warning("a new workout could not be discarded")
            raise
