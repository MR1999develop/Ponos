# 016. Availability is days and sessions per week, not minutes per day

- Status: Accepted
- Date: 2026-11-09

## Context

Until now the user told the plan how many minutes were available on each weekday
(`day0_min` to `day6_min`), plus the day of the long session and the most training in a week
(ADR 005 and the phase 5 schema). The plan put a session on every day with minutes and never went
over them.

The user asked (2026-11-09) to choose instead the days on which they can train, how many sessions
a week they want, the day of the long session and the hours at most in a week, and to stop giving
minutes per day. The saved availability changes shape, so this is a decision that is hard to
undo: a profile on disk carries it, backups carry it, and the plan engine reads it.

Constraints: the plan stays pure and deterministic (same data, same plan); it never exceeds the
availability; hard sessions never on consecutive days; the long session is the key session of the
week.

## Options

### What the user gives

1. **Keep minutes per day.**
   - Pro: nothing to migrate; the user controls the length of every day.
   - Con: seven numbers for a choice that is really "which days and how many"; a day with 0 means
     "not available" and a day with 30 means "available but short", which users did not mean; the
     user is asked for a figure they rarely know.
2. **Days available, sessions per week, long day, hours at most in a week.**
   - Pro: it is what the user decides; no number to guess; the plan's own session types already
     have a maximum length, so the day's length follows from the type.
   - Con: the user no longer says "on Tuesday I only have 40 minutes"; a day is as long as the
     session type allows; the saved shape changes.
3. **Days only, the number of sessions derived from the weekly hours.**
   - Pro: one number less.
   - Con: the number of sessions would depend on the load of the week, so two weeks with the same
     hours could have a different shape; harder to explain and to test.

### Which days get a session when there are fewer sessions than days

1. **The first days of the week.** Con: sessions on consecutive days, which breaks the hard and
   easy alternation.
2. **The user marks them one by one.** Con: that is the "minutes per day" problem again.
3. **Spread by rule.** The long day first, then each time the available day farthest from the
   ones already chosen, with the week as a circle (Sunday is next to Monday); on a tie the lowest
   weekday.
   - Pro: deterministic, even spacing, consecutive days avoided when the days allow it; no data.
   - Con: the user cannot ask for a specific pair of days among the available ones (they remove the
     others from the available days).

### The length of a day

1. **A cap per weekday saved by the user.** Con: the minutes per day again.
2. **The longest session of any type**: `DAY_MAX_MINUTES` = 240, the maximum of the long session.
   - Pro: one constant in the plan's defaults; each session still has the maximum of its own
     type.

### The long session

1. **Free: any session of the week can be the longest.** Con: a long run of 60 minutes next to an
   easy run of 90 turns the week upside down.
2. **Always the longest session of the week.** An easy or recovery session is at most 75% of the
   long one (`OTHER_SESSION_MAX_SHARE_OF_LONG_PERCENT`) and the other types are shorter than it;
   when the hours or the load do not allow all, the others are shortened first.

## Decision

- Option 2 for what the user gives: `WeeklyAvailability(days, sessions_per_week, long_day,
  max_week_minutes)`. The long day is always one of the days; sessions are between 1 and the
  number of days; with no day there is no session and no time (the empty availability, "nothing
  set").
- The days of the sessions: option 3 (`session_days`).
- A day holds at most `DAY_MAX_MINUTES` (240) and each session at most the maximum of its type.
- The long session is the longest of its week; easy and recovery sessions are at most 75% of it;
  the others give way first.
- Saved format, **schema 12**: `weekly_availability` has `days_mask` (bit i = weekday i, Monday =
  bit 0), `sessions_per_week`, `long_day`, `max_week_min`, with the same rules as `CHECK`
  constraints. The migration from 11 runs in one transaction after the usual copy of the database
  in `backup/`: the days are those that had minutes, the sessions are as many as the days, the long
  day is added to the days if it was not one, the weekly maximum is kept; a row with no minutes at
  all becomes the empty availability. SQLite cannot drop the old columns with their constraints, so
  the table is rebuilt.
- `METHOD_VERSION` is 6: the plan's shape changes for the same inputs. The saved plan is derived
  and carries its method; one saved with an older method stays until the next rebuild replaces it
  (the code does not compare the versions).

## Consequences

- Migrated users get as many sessions as they had days with minutes, so their plan keeps about the
  same shape; the lengths of the days are no longer theirs but the types' (an upper bound, not a
  target: the load of the week decides).
- The saved minutes of each day are lost in the migration. The copy in `backup/` keeps them.
- The settings page, the undo of the availability, the backup format (the database is inside it)
  and the plan engine read the new shape. The backup of a profile made before the change opens
  after the migration of its database, as every older profile does.
- The model of the day length is now a constant in the engine, so a different limit is a change of
  `defaults.py` and a new `METHOD_VERSION`.
- Spreading the sessions by rule means that two users with the same days and sessions get the same
  days: the choice is predictable and testable, but not personal.

## How compliance is verified

- `WeeklyAvailability` refuses an invalid combination at construction (days, sessions, long day,
  time); `session_days` has examples and a property test (same input, same days; the long day is
  always in; consecutive days are avoided when the days allow it).
- The migration test builds a schema 11 database with each case (minutes on some days, the long
  day without minutes, no minutes at all) and checks the result and the `CHECK` constraints.
- The invariants of every generated plan (`tests/planning/invariants.py`): only the days of
  `session_days`, never over the hours of the week, the long session the longest of its week, easy
  and recovery sessions at most 75% of it.
- The test of the stored `METHOD_VERSION` follows the constant.
