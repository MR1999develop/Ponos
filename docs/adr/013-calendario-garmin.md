# 013. Garmin calendar: sending the plan and deleting workouts

- Status: Accepted
- Date: 2026-11-08

## Context

ADR 002 puts Garmin behind ports and names `WorkoutCalendar` as one of them. Phase 7 sends the
sessions of the plan to the Garmin calendar as structured workouts and lets the user delete
scheduled workouts, with the means to get them back. Garmin has no official API for this: the only
way is the unofficial `garminconnect` 0.3.20, whose workout methods were read but not tried against
the live service (`experiments/garmin_workouts.md`). Three choices are hard to change once the user
has workouts on their watch: how a session is described to Garmin, how the app recognises and
protects what is the athlete's, and what is kept locally of a deletion.

## Options

### Shape of a workout

1. **Flat steps, each repetition written out; heart-rate range in bpm as main target, pace range
   as secondary target.**
   - Pro: nothing that depends on the least documented parts of the format (repeat groups); exact
     durations; the plan's own zones (threshold-based), not Garmin's.
   - Con: an interval session shows more steps on the watch.
2. **Repeat groups and Garmin's zone numbers.** Pro: tidier on the watch. Con: the zones would be
   Garmin's, not the plan's; the repeat group's "skip the last rest" flag is unverified.

### Idempotence of a send

1. **Look at the calendar first: same name on the same day is not created again; after a
   transient error, look before creating again.** Pro: no table, no id to keep in step with Garmin;
   works after a lost answer. Con: one extra request per send (and per recovery).
2. **A local table of sends.** Pro: no look-up. Con: a second truth that drifts when the user
   edits the calendar on Garmin.

### Deleting

1. **Save the definition in the profile first, then remove; creating again makes a new workout
   from the copy.** Pro: works for the athlete's own workouts too; the copy outlives Garmin's
   deletion. Con: the new workout has new ids.
2. **Only unschedule, never delete from the library.** Pro: nothing is lost. Con: the library
   fills with orphans of the app.

## Decision

- Option 1 for each of the three.
- **Recognition**: every workout the app creates is named `"Ponos · " + type + minutes`
  (`WORKOUT_PREFIX`). The app deletes a library workout only if it carries the prefix; a workout
  of the athlete is only taken off the calendar and stays in the library.
- **Limits** (constants in `planning/calendar_use_cases.py`): at most 60 workouts per send (the
  earliest days go first, the rest is reported as deferred), 100 per deletion, 60 per re-creation;
  the pauses, the backoff and the attempts are the `SyncPolicy` of the synchronisation; a 429 or a
  transient error is retried with backoff up to the policy's attempts and then the run stops;
  authentication required, cancellation, an unsupported operation and a copy that cannot be saved
  stop the run too. A workout Garmin refuses fails alone.
- **Order of a deletion**: read the definition, save the copy, check the calendar entry is still
  the one chosen, unschedule, delete from the library. Nothing is deleted without its copy, and a
  day before today is never touched.
- **Schema**: one table, `workout_backup` (id, name, day, definition text and its SHA-256, saved
  and restored times). It was written as a provisional "v9" in `storage/schema_workout_backup.py`
  and is renumbered when merged with the other v9 (injuries). Restored copies are forgotten after
  30 days; at most 1000 copies wait.
- **Incomplete adapter**: `WorkoutCalendar` is separate from `ActivitySource`;
  `GarminConnectSession.calendar` is `None` if the client cannot do workouts, and every operation
  may raise `CalendarUnsupportedError`.

## Consequences

- A workout created again has new Garmin ids and, if it was the athlete's, the name it had but no
  prefix.
- The response shapes are those inferred from the library; if Garmin's differ, only
  `garmin/workout_json.py` changes. `T-MAN-01` (real send and deletion, by the user) is the check.
- The copy is the only place that holds a definition after Garmin deletes it: it is in the
  profile's database with the other data, in the clear, like them (ADR 007).

## How compliance is verified

- Conversion: durations, heart-rate ranges and pace ranges of a session are those of the steps
  (property tests, `tests/planning/test_workouts.py`, `tests/garmin/test_workout_json.py`).
- T-SEC-10: only the chosen workouts are deleted, each after its copy; past days are refused; a
  stale entry is refused (`tests/planning/test_calendar_use_cases.py`,
  `tests/garmin/test_workouts.py`).
- T-SYNC-03 and T-SYNC-04 for the calendar: 429 with `Retry-After`, bounded attempts, transient
  errors without duplicates, authentication, cancellation (same file).
- Contracts of the port and of the copy store on the fake and the real implementation
  (`tests/contracts/test_workout_contracts.py`).
