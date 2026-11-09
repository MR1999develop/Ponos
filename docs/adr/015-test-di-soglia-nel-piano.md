# 015. The threshold test is a session of the plan

- Status: Accepted
- Date: 2026-11-09

## Context

ADR 009 made the threshold test a function of its own: the user picks an activity (or the app
finds one), the protocol (30 or 20 minutes) is a setting, and the plan only proposed where a
retest could go (an easy week of the retest window, or "before the plan starts" with no test).
The plan engine (`METHOD_VERSION` 4) never scheduled the test.

The user asked (2026-11-09): "the threshold test goes at the start of a training plan, in one of
the first sessions". Friel (FRI5, 5th edition) advises a test at the start of a period and every
4 to 6 weeks; the retest window of ADR 009 (42 to 56 days) already follows that.

Constraints are those of the plan: pure, deterministic engine, no health data in logs, never more
than the user's availability, hard sessions never on consecutive days, no intense session in the
72 hours before a race, nothing that loads an injured zone.

## Options

### How the test is represented in the plan

1. **A flag on the planned session** (`PlannedSession.threshold_test: ThresholdProtocol | None`),
   with the type `THRESHOLD`.
   - Pro: the weekly rules that count hard and intense sessions apply with no new case; the
     session types the user chooses stay as they are; the workout for the watch gets its own
     name from the flag.
   - Con: the saved shape of a session has one more field.
2. A new `SessionType.THRESHOLD_TEST`.
   - Pro: explicit.
   - Con: the type is saved with the user's choices and offered in the settings; every table keyed
     by session type (shares, priorities, models) needs an entry; the user would "choose" it.
3. A new block role for the test block.
   - Con: the session would still be a threshold session of unknown kind; no way to tell a
     test from a threshold interval of one block without reading the minutes.

### What decides that a test is due

1. The user asks for it. Con: the user is the one who forgets it.
2. **The retest window of ADR 009** (`retest_status`): due with no test, with the window open and
   with the window gone; not due with a recent test.
   - Pro: one rule for the proposal and the schedule.

### A test that went by undone

1. Plan it again every week until it is done. Con: a user who does not want it is nagged forever.
2. **Once more, the week after, and then stop.**

## Decision

- Option 1 for the representation: `PlannedSession.threshold_test`, a session of type `THRESHOLD`
  that is not one of the types the user chose (the test is a measurement, not a type: it is
  planned even if the user chose no threshold session). Structure: 15 min warm-up in Z2, the test
  (30 or 20 min, in Z4 as the expected zone), 10 min cool-down in Z1; 55 or 45 minutes.
  The Garmin workout is named "Threshold test <minutes> min".
- Due: the retest status is not `NotYet`, the plan's sport has a test (running, trail, cycling),
  there is no open injury or illness and none closed recently (`ramp_reduced`), and the test was
  not planned and missed twice. The protocol is the one the user chose in the settings
  (`PlanInputs.test_protocol`).
- Where: the earliest day, from tomorrow, of the first week that has one, among the days of
  normal load (base, build, maintenance, peak; never a taper, a recovery or an unload week) with
  time for the whole session, not in the 72 hours before a race, and not next to a hard day (the
  long session, a race, the last hard day of the week before): so it is preceded and followed by
  an easy day or a rest. The long session is placed first, then the test, then the other quality
  sessions. If the first week has no such day the engine tries the second week; then it stops.
- A hard session: it counts as an intense session and a hard day in the weekly limits (the other
  intense sessions make room for it; it is always allowed, even in a week of two sessions), it is
  left out of the 80/20 share by time (a measurement, once a period), and its load is paid from the
  week's load budget, so it is in the ramp and in the projected PMC.
- Done: the plan has no memory. When the activity of that day is applied as a test, the retest
  status becomes `NotYet` and the next rebuild has no test. If the day goes by with no test
  applied, the caller gives the engine `PlanInputs.missed_tests` (computed by
  `missed_threshold_tests` from the frozen days and the applied tests): with one missed test the
  engine plans the test again, from the Monday after the week it was missed in; with two, never.
- `Plan.retest_proposal` is `RetestScheduled(day)` when the plan has the test; the older
  proposals remain for what the plan does not schedule (a recent test whose window falls in the
  plan, a test given up).
- `METHOD_VERSION` is 5. The plan has two more inputs (`test_protocol`, `missed_tests`) and
  four more parameters in its snapshot (warm-up, cool-down, weeks of attempt, planned at most).

## Consequences

- The saved shape of a session has a new field, so a plan saved with method 4 does not read back
  (the decoder wants exactly the fields): the shell rebuilds it at the next rebuild, as it did for
  methods 1 and 3. The frozen days written by method 4 are shown as unreadable in the adherence
  (what was done, no planned load), like any frozen day that cannot be read: acceptable before the
  first release, to be redone as an expand-contract change once there is one.
- The shell that builds the inputs (`profiles.RebuildPlan`) must pass the user's protocol and
  the missed tests. Until it does, the engine plans the 30-minute test and never knows a test was
  missed (so it keeps planning it from tomorrow).
- The interface needs a text for a session that is a threshold test and one for
  `RetestScheduled`.
- The test is not a type the user chose, so a check "the plan only has chosen types" must exempt
  it (the plan's own invariants do).
- Retests inside a running plan (the window opens in week 7) are still only proposed, in an
  easy week; scheduling them is a later decision.

## How compliance is verified

`tests/planning/test_threshold_test_session.py` (T-PIANO-10): the scenarios above in the words of
the domain; `tests/planning/invariants.py` checks every generated plan (property test): at most one
test, never with a recent test, an injury or two missed tests, never in the 72 hours before a race
or next to a hard day, the protocol the user chose; the garmin workout name and steps.
