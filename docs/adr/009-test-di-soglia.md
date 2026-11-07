# 009. Method of the threshold test

- Status: Accepted
- Date: 2026-11-07

## Context

Phase 4 adds the 30-minute running threshold test. From one recorded activity
the app derives a threshold heart rate and a threshold pace, updates the HR
zones through a dated measurement, adds pace zones and proposes a retest. The
user sees the numbers and the load of every later activity depends on the
threshold, so the choices below are hard to change after release: how the test
is identified, which part of the activity is used, how the figures are
computed, when a test is refused, how the pace zones are defined, and how the
result is applied and undone.

This ADR adds schema version v5 to ADR 005 and builds on ADR 004 (load model)
and ADR 007 (dated measurements and HR zones). The rest of them remains valid.

Constraints:

- the app is not a medical device: the zones are a training indication;
- the evaluation is pure and deterministic: same activity and same limits,
  same outcome, with no I/O, clock or randomness in the domain;
- a test that is not reliable is refused with a reason, never repaired;
- threshold HR and pace are health data: only in the profile database, never
  in logs, messages or error chains;
- the history of the tests is user data and must survive a rebuild of the
  activity index (which is derived);
- every number is a project default (Friel's protocol, adapted) and lives in
  one file, `assessment/domain/defaults.py`.

Source: Friel, *The Triathlete's Training Bible* (30-minute test, threshold HR
from the last 20 minutes, zones as shares of the threshold). The pace-zone
percentages are project defaults derived from his zones; they are not taken
from a table of the book.

## Options

### How the test is identified

1. **The user says which activity is the test; running only (`RUNNING`,
   `TRAIL`).**
   - Pro: the user knows what they did; no guess can apply a wrong threshold;
     the rule is easy to explain and to test.
   - Con: one more step after the run; a user who forgets does not get a
     result.
2. **Automatic recognition (a 30-minute effort found in the history).**
   - Pro: no step for the user.
   - Con: a hard race, a tempo run or a long interval session looks like a
     test; a wrong recognition changes the threshold and every later load
     without the user noticing.

### Which part of the activity is used

1. **The 30 minutes of elapsed time with the greatest distance covered; with
   no usable distance, the 30 minutes with the highest valid mean HR.**
   - Pro: the effort is found without help; warm-up and cool-down fall out by
     themselves; the maximum of a piecewise linear function lies at a
     breakpoint, so only those are tried (no per-second loop).
   - Con: a fast downhill or a tailwind wins over a steady effort; with no
     distance the highest HR may be a spike near the end.
2. **A lap pressed by the user at the start of the test.**
   - Pro: the exact start.
   - Con: depends on a watch setting and on the habit of pressing it; a
     missing or late lap makes the test unusable; the file may not contain
     laps (GPX).
3. **The last 30 minutes of the activity.**
   - Pro: trivial.
   - Con: wrong when there is a cool-down or the test is followed by a
     second block.

### How the threshold HR is computed

1. **Time-weighted mean of the valid HR samples in the last 20 minutes of the
   segment (Friel), rounded to a whole bpm.**
   - Pro: the first 10 minutes are discarded as the settling of the effort;
     weighting by time is not affected by an irregular sampling; the same
     validity rule as the load (inside `[FCmin, FCmax]`, intervals up to 10 s).
   - Con: it is a field estimate, not a lactate test; a poor pacing in the
     last 20 minutes changes it.
2. **Mean of the whole segment.**
   - Pro: more samples.
   - Con: includes the rise of the HR at the start, so it underestimates.

### Threshold pace

1. **Time over distance of the same 20 minutes, in whole seconds per
   kilometre, absent if the distance does not cover them or the pace is
   outside 90-3,600 s/km.**
   - Pro: pace and HR describe the same minutes; a GPS glitch or a walk does
     not become a pace.
   - Con: GPS and treadmill sensors have errors; the pace is only as good as
     the distance.
2. **Pace of the whole segment.**
   - Pro: simpler.
   - Con: not the same minutes as the HR.

### Pace zones

1. **Five zones aligned with the HR zones, as a share of the threshold speed:
   Z1 below 78%, Z2 78-88%, Z3 88-94%, Z4 94-100%, Z5 100% and over.**
   - Pro: one idea for HR and pace; the five zones match those of the HR
     (ADR 007) and the percentages are derived from Friel; speed is linear in the share, pace is not, and
     the comparison is done on integers.
   - Con: the percentages are project defaults and not a published table; a
     share of speed is less intuitive than a pace range, so the app also gives
     the pace boundaries.
2. **Daniels / VDOT zones.**
   - Pro: a published system with a long history.
   - Con: needs a race result or a VDOT value, not a 30-minute threshold; its
     zone names and purposes do not match the five HR zones of the app.
3. **Threshold estimated from race results.**
   - Pro: uses data many runners have.
   - Con: depends on the race distance, course and conditions; the app would
     need a race record that does not exist yet; it is no test.

### Refusals

1. **Refuse with the reason: not a run, shorter than 30 minutes, no HR, more
   than 60 s of pauses in the segment, less than 90% valid HR in the last 20
   minutes, threshold outside the profile limits.**
   - Pro: a refused test never changes the zones; the user learns what to
     redo.
   - Con: a test with a short stop for a traffic light is refused above the
     limit; the limits are defaults and may feel strict.
2. **Accept and flag the weak ones.**
   - Pro: the user decides.
   - Con: the threshold moves every later load; the user cannot judge sample
     coverage.

### Retest

1. **Proposed from 42 to 56 days after the last applied test.**
   - Pro: the 6-8 weeks of the protocol, as exact days that are testable at
     the edges (41/42/56/57).
   - Con: the app proposes, it does not force; the recovery week arrives with
     the plan in phase 5, so for now only the window of dates is used.
2. **A fixed calendar (every March and September).**
   - Pro: simple.
   - Con: ignores when the last test was done.

### How the result is applied

1. **The threshold becomes a dated measurement on the test day, with an exact
   undo; the history is kept in its own table (schema v5).**
   - Pro: it reuses the rules of `UpdateMeasurements` (not in the future, HR
     consistent, valid until the next entry) instead of copying them; apply is
     one transaction; the undo restores the previous entry of that day, so the
     state is exactly the previous one; the loads recalculate through the
     parameter fingerprint, as for any measurement.
   - Con: the undo is refused if the user edited the entry after the test; the
     same day as an existing entry replaces it.
2. **Overwrite the current threshold and keep no history.**
   - Pro: nothing new in the schema.
   - Con: no undo, no retest date, past activities would use the new threshold.

## Decision

- **Identification:** the user chooses the activity; running only. Nothing is
  recognised automatically.
- **Segment:** 1,800 s of elapsed time; greatest distance, otherwise highest
  valid mean HR; first one on a tie; inside the data.
- **Threshold HR:** time-weighted mean of the valid samples of the last 1,200 s
  of the segment, rounded to a whole bpm (half up). The HR limits are those in
  force on the local day of the start.
- **Threshold pace:** time over distance of the same 1,200 s, whole seconds per
  kilometre, half up; absent without distance covering them or outside
  90-3,600 s/km.
- **Refusals, in this order:** wrong sport; too short (1,799 s is too short,
  1,800 s is enough); no HR; pauses (gaps over 10 s between records) adding up
  to more than 60 s in the segment (60 s is allowed); valid HR under 90% of the
  time of the last 20 minutes; threshold not strictly between `FCmin` and
  `FCmax`. An unreadable file is a different result (excluded activity), not a
  refusal.
- **Pace zones:** the five zones above, with the lower limit belonging to the
  faster zone and integer comparisons.
- **Retest:** 42 to 56 days after the last applied test, both included; no
  test, not yet, recommended, overdue.
- **Apply and undo:** `ApplyThresholdTest` saves the history entry and the
  dated threshold in one transaction; a test already stored is refused.
  `RemoveThresholdTest` restores the entry the test replaced, or deletes the
  one it created, in one transaction; it refuses if that entry changed since.
- **Data:** the distance series is cumulative metres on the same time axis as
  the HR (from the FIT `distance` field, otherwise from `speed` integrated
  without inventing distance over gaps above 10 s, or from the GPX track by
  haversine). A series that is not finite or goes backwards is not used and
  not repaired: the activity stays valid, without pace. History in the table
  `threshold_test` (schema v5, safety copy before migrating), one row per
  activity, with the version of the method.
- **Constants:** every number is in `assessment/domain/defaults.py`.
  `METHOD_VERSION` changes when the way the result is computed changes. The
  detail is in the project skill, `references/test-soglia.md`.

## Consequences

- The threshold of a user can be wrong: a field test, a hard day, a downhill
  segment. The refusals and the explicit choice of the activity reduce the
  risk; they do not remove it. The undo is always available.
- A change of any constant changes the outcome of every future test. Tests
  already applied keep their stored figures and their `method_version`; they
  are not recomputed. A change goes through `defaults.py`, the T-PIANO-06
  tests and the skill reference in one modification.
- Changing the threshold changes the loads of the activities from the test day
  to the next entry. They are recomputed on their own by the parameter
  fingerprint; the cache of the load is unchanged.
- Schema v5 adds `threshold_test` without a foreign key to `activities`: the
  index is derived and regenerable and the history must survive a rebuild.
  Activity key, day, threshold and pace are checked by `CHECK` constraints.
- The distance series is a new input from untrusted files (threat model, B2,
  D-F4).
- The test is running only. A bike test, and a rule for the recovery week, need
  a new ADR.
- This ADR is not changed after acceptance. A different method replaces it
  with a new ADR.

## How compliance is verified

- T-PIANO-06: threshold HR and segment on synthetic traces (warm-up, test
  stopped, too short, HR missing or out of limits, no distance, pauses),
  retest at 41, 42, 56 and 57 days, apply and undo restoring the exact state,
  property tests against a naive implementation (the threshold lies between
  the minimum and the maximum of the valid samples of the window; the chosen
  window has the greatest distance), FIT fixtures with distance, migration v5
  and a contract test of the history (`tests/assessment/`).
- Constants in one file: `assessment/domain/defaults.py`, checked in code
  review; no automatic test yet.
- `method_version`: saved with every test; a change of the method raises
  `METHOD_VERSION` and the test of the saved value.
- T-DATI-01: migration v4 to v5 interrupted at every statement, with the safety
  copy.
- `import-linter` in the CI: the assessment domain stays pure.
