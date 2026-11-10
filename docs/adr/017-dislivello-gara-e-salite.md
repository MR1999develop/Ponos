# 017. The elevation gain of a race changes the estimate and adds hill sessions

- Status: Accepted
- Date: 2026-11-10

## Context

A race had a date, a priority, a sport, a distance and an optional target time. The user asked
(2026-11-10): "when you add a race, add the elevation gain too". A mountain race of 30 km with
1500 m of climb is not a flat 30 km: it takes longer, loads the body more and is trained for
differently. The user chose two effects of the new field: it changes the estimate of the load, and
it makes the plan put hill sessions in the preparation.

The saved race gains a column, and the plan JSON gains fields, so part of this is hard to undo: a
profile on disk carries the column, backups carry it, and saved plans carry the new fields.

Constraints: the plan stays pure and deterministic; races saved before keep their meaning (a flat
race); the weekly distribution (80/20, hard days, no hard session in the 72 hours before a race)
and the availability are never broken by a hill session; the session types are a choice of the
user and are stored under `CHECK` constraints.

## Options

### How a hill session is represented

1. **A new `SessionType` ("hills").**
   - Pro: the user could choose it, and the calendar would tell it apart without new fields.
   - Con: the types are saved in the database under `CHECK` constraints (choices, plan days), so a
     new one means a migration of those tables; every rule that lists the types (hard days, intense
     sessions, the 25% limit, the 80/20 share) needs a decision for the new type, and each is a
     place to get wrong; the user would pick it even for a flat race.
2. **A variant of the existing types: `Shape.slope_m_per_km` and `PlannedSession.ascent_m`.**
   - Pro: a hill-repeats session keeps the type of the quality session it replaces and a hilly long
     run stays a long run, so the 80/20 rule, the hard days and the weekly limits are unchanged
     without new code; no type in a `CHECK`; plans saved before still read (the field defaults
     to 0).
   - Con: "hill" is not a type but a property, so a reader of the calendar sees it only from the
     name and the elevation gain; the code has to ask `is_hill_repeats` and `is_hilly_long`.

### Which distance sizes the race

1. **The real distance, the climb shown but with no effect.**
   - Pro: nothing changes for the engine.
   - Con: a race with 1500 m of climb is expected to take as long as a flat one: the load, the race
     pace of the sessions and the verdict on the target time are wrong, which is the opposite of
     what the user asked.
2. **A flat-equivalent distance: distance + 10 × climb** (`ASCENT_EQUIVALENT_M_PER_M` = 10, so 100
   m of climb count as 1 km, the "km-effort" rule of thumb of trail races).
   - Pro: one number; time, pace and verdict follow with the code that already exists
     (Riegel on a distance).
   - Con: a rule of thumb, not a model: it ignores the grade, the descents and the fatigue of
     long climbs, and it is a project default that has not been validated on the user's data.
3. **A model of the grade (for example Minetti's cost of running uphill).**
   - Pro: closer to the physiology.
   - Con: needs the descents too (the user gives only the positive gain); the parameters would be
     as unvalidated as the single factor of option 2, with more code.

The equivalent distance is used only where the effort of the race is what matters. The race class
(taper and recovery length), the CTL target of the distance table and the `MissingLongSession`
warning stay on the real distance: they depend on how long the race lasts on the ground and how
far the user has to be able to go, not on the climb.

## Decision

- `RaceGoal.ascent_m`, whole metres from 0 to `MAX_ASCENT_M` (30 000: the Tor des Geants has about
  24 000); the mean grade is at most `MAX_MEAN_GRADE_PERCENT` (50%). 0 is a flat race and the
  default.
- Saved format, **schema 16**: `goal.ascent_m INTEGER NOT NULL DEFAULT 0` with a `CHECK` between 0
  and 30 000 (`RACE_ASCENT_MIGRATION`, `storage/schema_race_ascent.py`, after the usual copy of the
  database in `backup/`). Races already saved have 0.
- The expected time of the race (the target time, else the best time possible, else the default
  pace), the race pace of the sessions and the verdict on the target time use
  `equivalent_distance_m`. The load of the race follows from its expected time.
- Hill sessions (option 2 of the representation), only when all of these hold:
  - the race is the A race and the plan is a running plan (running or trail);
  - the mean grade is at least `HILL_MIN_SLOPE_M_PER_KM` (20 m/km);
  - no injury or illness limits the running or the intensity;
  - the day is in the build or the peak (`HILL_PHASES`), never in the base, the final taper or an
    unload week of the 3:1 cycle.
- In those weeks, one hill-repeats session replaces the first quality session of the week
  (build: 15' Z2, 6 to 10 x 2' Z5 with 3' Z1, 10' Z1; peak: 4 to 8 x 3' Z4), and every long run is
  a hilly long run (same duration and zones). The type of the session does not change. Heart rate
  only, no pace. The indicative climb is 10 m per minute of work for the repeats and hours x 7 km/h
  x the mean grade of the race for the long run, to the nearest 10 m.
- The Garmin workouts are called "Hill repeats N min" and "Hilly long N min".
- `METHOD_VERSION` is 8: the plan changes for the same inputs. `PlannedRace.ascent_m` and
  `PlannedSession.ascent_m` default to 0, so plans and frozen days saved before still read.

## Consequences

- A race with climb is expected to take longer and weighs more in the load and in the projection;
  its target time is judged against the longer best time. A race with no climb gives the plan it
  always gave.
- The three numbers (10 m of equivalent per metre of climb, 7 km/h and 10 m per minute of work) are
  estimates and not validated. Changing one is a change of `defaults.py` and a new
  `METHOD_VERSION`.
- The calendar tells a hill session only by its name and its "Elevation gain: about N m" line.
- The race dialog, the goals table, the text of the race and the texts of the plan show the
  elevation gain; the Italian translations follow.
- A hill session is not a new type, so the user cannot ask for or refuse it by type; they can only
  leave the race flat or set the climb. An injury that limits running or intensity stops the hill
  work with the rest of the intensity.
- Backups made before the change open after the migration of their database, like any older
  profile; the plan of an older method stays until the next rebuild.

## How compliance is verified

- `tests/planning/test_race_ascent.py` (T-PIANO-11):
  - the equivalent distance (100 m of climb are 1 km) and the expected time, load, race pace and
    default pace computed on it;
  - the threshold on and off points (199 m and 200 m in 10 km, that is 19.9 and 20 m/km);
  - hill work only in the build and the peak, never in an unload week, the base or the taper;
    one hill-repeats session a week, no pace targets, every long run of a loaded week hilly;
  - no hill work for a B race beside a flat A race, for a race that is not a run, or while an
    injury limits the running or the intensity; it comes back at the last step of the path;
  - determinism, and a property test: the invariants of `tests/planning/invariants.py` hold for
    random distances, grades, weeks, types and days.
- `tests/storage/test_race_ascent_store.py` (T-DATI-01): the released migration has not changed,
  a profile from before it keeps its races as flat ones and gets a copy, the `CHECK` constraint
  refuses an invalid gain, the store saves and reads it back.
- `RaceGoal` refuses an invalid gain or an impossible grade at construction.
