# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin's JSON for workouts: built from a `WorkoutSpec`, read from the responses.

The shapes follow the `garminconnect` 0.3.20 code and its typed models (`workout.py`); they have
NOT been checked against the live service (no network call was made while writing this):
`experiments/garmin_workouts.md` lists what is unverified and `T-MAN-01` checks it. Everything
that is read comes from a response and is untrusted: sizes, types and characters are checked here
and an entry that does not fit is left out, never repaired.

Pace targets are in m/s, with the slower pace as the lower limit, as Garmin wants.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import date
from typing import Final

from ponos_coach.activities.domain import Sport
from ponos_coach.planning.domain.workouts import (
    ScheduledWorkout,
    StepKind,
    WorkoutSpec,
    WorkoutStep,
)
from ponos_coach.planning.workout_calendar import MAX_BACKUP_PAYLOAD_CHARS

MAX_ENTRIES: Final = 2_000  # entries of one calendar response that are read
MAX_NAME_CHARS: Final = 200
MAX_ID: Final = 10**15
_NAME_ILLEGAL: Final = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_DAY: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

_SPORT_TYPES: Final = {
    Sport.RUNNING: (1, "running"),
    Sport.TRAIL: (1, "running"),
    Sport.CYCLING: (2, "cycling"),
}
_OTHER_SPORT: Final = (3, "other")
_STEP_TYPES: Final = {  # kind -> (id, key, displayOrder), from the library's helpers
    StepKind.WARMUP: (1, "warmup", 1),
    StepKind.COOLDOWN: (2, "cooldown", 2),
    StepKind.INTERVAL: (3, "interval", 3),
    StepKind.RECOVERY: (4, "recovery", 4),
}
_TIME_CONDITION: Final = {
    "conditionTypeId": 2,
    "conditionTypeKey": "time",
    "displayOrder": 2,
    "displayable": True,
}
_HR_TARGET: Final = {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone",
    "displayOrder": 1,
}
_PACE_TARGET: Final = {
    "workoutTargetTypeId": 6,
    "workoutTargetTypeKey": "pace.zone",
    "displayOrder": 2,
}
# Keys of a library definition that belong to the account or to the copy, not to the workout.
_IDENTITY_KEYS: Final = ("workoutId", "ownerId", "createdDate", "updatedDate", "workoutSourceId")


class UnexpectedWorkoutResponseError(Exception):
    """A response does not have the shape the adapter needs. The message holds no response text."""


def sport_type(sport: Sport) -> dict[str, object]:
    """Garmin's sport type of a sport: running, cycling, or "other" for the rest."""
    type_id, key = _SPORT_TYPES.get(sport, _OTHER_SPORT)
    return {"sportTypeId": type_id, "sportTypeKey": key, "displayOrder": 1}


def _mps(s_per_km: int) -> float:
    return 1000.0 / s_per_km


def _step(index: int, step: WorkoutStep) -> dict[str, object]:
    type_id, key, order = _STEP_TYPES[step.kind]
    body: dict[str, object] = {
        "type": "ExecutableStepDTO",
        "stepOrder": index,
        "stepType": {"stepTypeId": type_id, "stepTypeKey": key, "displayOrder": order},
        "endCondition": dict(_TIME_CONDITION),
        "endConditionValue": float(step.seconds),
        "targetType": dict(_HR_TARGET),
        "targetValueOne": float(step.hr_low_bpm),
        "targetValueTwo": float(step.hr_high_bpm),
    }
    if step.pace_slow_s_per_km is not None and step.pace_fast_s_per_km is not None:
        body["secondaryTargetType"] = dict(_PACE_TARGET)
        body["secondaryTargetValueOne"] = _mps(step.pace_slow_s_per_km)
        body["secondaryTargetValueTwo"] = _mps(step.pace_fast_s_per_km)
    return body


def workout_payload(spec: WorkoutSpec) -> dict[str, object]:
    """The body of the request that creates `spec` in the library: one segment, one time-based
    step per step of the spec, heart-rate range as target and pace range as secondary target."""
    sport = sport_type(spec.sport)
    return {
        "workoutName": spec.name,
        "sportType": sport,
        "estimatedDurationInSecs": spec.total_seconds,
        "workoutSegments": [
            {
                "segmentOrder": 1,
                "sportType": dict(sport),
                "workoutSteps": [_step(i, s) for i, s in enumerate(spec.steps, start=1)],
            }
        ],
    }


# --- Reading responses --------------------------------------------------------------------------
def _positive_id(value: object) -> int | None:
    if type(value) is int and 0 < value < MAX_ID:
        return value
    return None


def _entry_id(raw: object, key: str) -> int:
    found = _positive_id(raw.get(key)) if isinstance(raw, Mapping) else None
    if found is None:
        raise UnexpectedWorkoutResponseError("response without a usable id")
    return found


def parse_workout_id(response: object) -> int:
    """The id of a created workout (`workoutId`)."""
    return _entry_id(response, "workoutId")


def parse_schedule_id(response: object) -> int:
    """The id of a schedule entry (`workoutScheduleId`)."""
    return _entry_id(response, "workoutScheduleId")


def _clean_name(value: object) -> str | None:
    if not isinstance(value, str) or not 0 < len(value) <= MAX_NAME_CHARS:
        return None
    return None if _NAME_ILLEGAL.search(value) else value


def _day_of(value: object) -> date | None:
    if not isinstance(value, str) or _DAY.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_entry(raw: object) -> ScheduledWorkout | None:
    """One calendar entry as a scheduled workout, or None if it does not fit.

    Expected: `workoutScheduleId`, `calendarDate` (`YYYY-MM-DD`) and a nested `workout` with
    `workoutId` and `workoutName`.
    """
    if not isinstance(raw, Mapping):
        return None
    workout = raw.get("workout")
    if not isinstance(workout, Mapping):
        return None
    schedule_id = _positive_id(raw.get("workoutScheduleId"))
    workout_id = _positive_id(workout.get("workoutId"))
    name = _clean_name(workout.get("workoutName"))
    day = _day_of(raw.get("calendarDate"))
    if schedule_id is None or workout_id is None or name is None or day is None:
        return None
    return ScheduledWorkout(schedule_id, workout_id, name, day)


def parse_entries(response: object) -> tuple[list[ScheduledWorkout], int]:
    """The usable entries of a calendar response and the number left out.

    Raises:
        UnexpectedWorkoutResponseError: the response is not a list, or has too many entries.
    """
    if not isinstance(response, list) or len(response) > MAX_ENTRIES:
        raise UnexpectedWorkoutResponseError("calendar response not interpretable")
    found = [entry for entry in map(parse_entry, response) if entry is not None]
    return found, len(response) - len(found)


def entry_matches(response: object, item: ScheduledWorkout) -> bool:
    """True if a schedule response is the entry `item` describes: same workout and same day."""
    entry = parse_entry(response)
    return entry is not None and (entry.workout_id, entry.day) == (item.workout_id, item.day)


# --- Definitions kept for a later copy ----------------------------------------------------------
def definition_text(response: object) -> str:
    """The definition of a library workout as JSON text, to keep and to create again.

    Raises:
        UnexpectedWorkoutResponseError: not an object, or longer than the backup allows.
    """
    if not isinstance(response, Mapping):
        raise UnexpectedWorkoutResponseError("definition not interpretable")
    try:
        text = json.dumps(response, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as error:
        raise UnexpectedWorkoutResponseError("definition not interpretable") from error
    if len(text) > MAX_BACKUP_PAYLOAD_CHARS:
        raise UnexpectedWorkoutResponseError("definition too big")
    return text


def definition_payload(text: str) -> dict[str, object]:
    """The body that creates a new workout from a kept definition: the same workout without the
    ids and dates of the original.

    Raises:
        UnexpectedWorkoutResponseError: too long, not JSON or not an object.
    """
    if len(text) > MAX_BACKUP_PAYLOAD_CHARS:
        raise UnexpectedWorkoutResponseError("definition too big")
    try:
        loaded = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise UnexpectedWorkoutResponseError("definition not interpretable") from error
    if not isinstance(loaded, dict) or _clean_name(loaded.get("workoutName")) is None:
        raise UnexpectedWorkoutResponseError("definition not interpretable")
    return {str(k): v for k, v in loaded.items() if k not in _IDENTITY_KEYS}
