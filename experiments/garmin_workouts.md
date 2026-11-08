# Garmin workouts: what `garminconnect` 0.3.20 offers

Written on 2026-11-08 by reading the installed library (`garminconnect/api/workouts.py`,
`api/training.py`, `workout.py`). **No request was made to Garmin**: nothing here was checked
against the live service, except what the 2026-11-06 spike recorded (the calendar-by-period call
works and returns a JSON list, empty for that account; see `garmin_spike_shapes.json`). Everything
marked "unverified" is checked by `T-MAN-01`. No personal data.

## Methods (all on `garminconnect.Garmin`)

| Need | Method | Request |
|---|---|---|
| Create a workout | `upload_workout(workout_json: dict \| list \| str) -> dict` | `POST /workout-service/workout` |
| Create (typed) | `upload_running_workout`, `upload_cycling_workout`, ... (need `pydantic`) | same, via `workout.py` models |
| Schedule on a day | `schedule_workout(workout_id, date_str: "YYYY-MM-DD") -> dict` | `POST /workout-service/schedule/{workoutId}` body `{"date": ...}` |
| List scheduled | `get_scheduled_workouts_by_period(start, end) -> list[dict]` | `GET /workout-service/schedule/workoutScheduleByPeriod?startDate&endDate` |
| List scheduled (month) | `get_scheduled_workouts(year, month) -> dict` (`calendarItems`, month 1-12 here, 0-based on the wire) | `GET .../calendar-service/year/{y}/month/{m}` |
| One scheduled entry | `get_scheduled_workout_by_id(scheduled_workout_id) -> dict` | `GET /workout-service/schedule/{scheduleId}` |
| Read a workout | `get_workout_by_id(workout_id) -> dict`, `get_workouts(start, limit, ...)` | `GET /workout-service/workout/{id}` |
| Take off the calendar | `unschedule_workout(scheduled_workout_id)` | `DELETE /workout-service/schedule/{scheduleId}` |
| Delete from the library | `delete_workout(workout_id)` | `DELETE /workout-service/workout/{id}` |
| Move | `reschedule_workout(scheduled_workout_id, cdate)` | `PUT /workout-service/schedule/{id}` |
| Replace in place | `update_workout(workout_id, workout_json)` | `PUT /workout-service/workout/{id}` |
| Copy | `copy_workout(workout_id, name)` | `POST /workout-service/workout/copy/{id}` |
| Send to the watch | `push_workout_to_device(...)`, `send_scheduled_workouts_to_devices()` | not used |

Also present and not used: `create_calendar_event` and `delete_calendar_event` (races and goals,
not workouts), calendar notes, training plans.

Complete: everything the app needs (create, schedule, list, read back, unschedule, delete) exists.
The app calls `upload_workout` with a plain dict (no `pydantic`), `schedule_workout`,
`get_scheduled_workouts_by_period`, `get_scheduled_workout_by_id`, `get_workout_by_id`,
`unschedule_workout` and `delete_workout`. Library limits: timeouts are the library's 15 s; a
response is read whole before it is returned; `upload_walking_workout` and
`upload_hiking_workout` always raise (Garmin has no such sport type), which is why every sport
other than running and cycling is sent as "other".

## JSON the app sends (built from the code of `workout.py`)

```json
{
  "workoutName": "Ponos · Threshold 55 min",
  "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
  "estimatedDurationInSecs": 3300,
  "workoutSegments": [{
    "segmentOrder": 1,
    "sportType": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "workoutSteps": [{
      "type": "ExecutableStepDTO",
      "stepOrder": 1,
      "stepType": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
      "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2, "displayable": true},
      "endConditionValue": 1200.0,
      "targetType": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone", "displayOrder": 1},
      "targetValueOne": 150.0,
      "targetValueTwo": 165.0,
      "secondaryTargetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone", "displayOrder": 2},
      "secondaryTargetValueOne": 2.5,
      "secondaryTargetValueTwo": 4.0
    }]
  }]
}
```

- Ids: sport 1 running, 2 cycling, 3 other; step 1 warmup, 2 cooldown, 3 interval, 4 recovery;
  end condition 2 = time (seconds); target 4 = heart rate (here custom bpm, no `zoneNumber`),
  6 = pace.
- The heart-rate range is in bpm (zones of the app, threshold-based), not Garmin's own zones.
- Pace is in m/s and the slower pace is the lower limit (`targetValueOne`).
- Repetitions of an interval session are written out step by step (work, rest, work...), not as
  a repeat group: a repeat group's "skip the last rest" flag is the part of the format least
  documented in the library.

## JSON the app expects back (unverified)

- `upload_workout` response: an object with an integer `workoutId`.
- `schedule_workout` response: an object with an integer `workoutScheduleId`.
- Calendar entries (by period and by id): `workoutScheduleId`, `calendarDate` (`YYYY-MM-DD`) and a
  nested `workout` with `workoutId` and `workoutName`. The library's `reschedule_workout` reads
  `workout.workoutId` and writes `calendarDate`, which is where this shape comes from.
- `get_workout_by_id`: the definition object; it carries `workoutId`, `ownerId` and dates, which
  the app drops before creating a copy again.

Anything that does not fit is left out of a list (never repaired) and a response without the ids
the app needs is a definitive error. If the real shape differs, only `garmin/workout_json.py`
changes. Fallback for the listing if the by-period call is wrong: the month call
(`calendarItems` with `itemType == "workout"`, `id`, `workoutId`, `title`, `date`).

## Design consequence: the adapter may be incomplete

- `WorkoutCalendar` is a separate port from `ActivitySource`; `GarminConnectSession.calendar` is
  `None` when the client cannot do workouts, and the synchronisation does not care.
- Every operation can raise `CalendarUnsupportedError` (a method gone from the library, or Garmin
  changing it); the use cases turn it into the stop reason `UNSUPPORTED` and keep what they did.
- `garmin/workout_client.py` is the whole surface used from the library: seven calls.
- Unknown, so handled defensively in the adapter: whether deleting a workout from the library also
  removes its calendar entries (a removal unschedules first, then deletes); and whether the
  calendar changed since the list (a removal reads the entry back and refuses if it is no longer
  the workout and day the user chose).
