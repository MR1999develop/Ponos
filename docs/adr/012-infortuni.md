# 012. Injuries, illness and the path back

- Status: Accepted
- Date: 2026-11-08

## Context

Phase 6 adds the injury questionnaire, the warning signs, the path back by steps and the daily
check-in, and ties them to the plan. Ponos Coach is not a medical device: it never diagnoses, it
asks, stops or reduces training and invites a visit. Three choices are hard to change once users
have data: what is saved (the path step, or only the answers), how the plan reacts, and what stops
the plan altogether.

Sources, by the codes of the project skill: CSM1 and CSM2 (Clinical Sports Medicine, vol. 1 and 2),
SIL (pain-guided return), FER25 (activation step). Numbers are in `injury/domain/defaults.py`, each
with its source or "project default".

## Options

### What is saved

1. **The answers only (questionnaire, check-ins, review day, closing day); the step is replayed.**
   - Pro: one source of truth; a rule change replays old data; undo is exact by restoring the record.
   - Con: the step costs a replay (at most 400 check-ins, microseconds).
2. **The step as a column, updated by each check-in.**
   - Pro: a plain read.
   - Con: two truths; a changed rule leaves wrong steps; undo must recompute.

Chosen: 1. The path is a fold of `advance` over the check-ins.

### How the plan reacts

1. **Restrict the session types the user chose, by step; no new types.** Running by grade (none,
   Z1 recovery, easy, easy and long, all), no intensity before the last step, sessions on alternate
   days in the first running steps, strength by zone, the alternative session only if every open
   problem tolerates one of the pain-free activities the user declared. The bike is a step of the
   path for the knee, the hip and the back, a free alternative elsewhere (CSM1).
   - Pro: deterministic; the plan never invents a load the user did not choose.
   - Con: coarse: one rule per grade, not per session.
2. **A separate rehabilitation plan.** Pro: finer. Con: a second engine, and conflicts with goals.

Chosen: 1. The plan is rebuilt from today with the step it has now; it does not guess future steps
(each check-in rebuilds it). The weekly ramp is +3 while any problem is open and for 28 days after
closing (CSM1: the risk is in the quick return).

### What stops everything

Any warning sign or a systemic illness leaves no session, with the reason and the invitation to see a
doctor; a warning sign of the zone is not "less". The alternative (stop only what loads the zone)
needs a table of what loads what that no source gives; it can come later without changing the data.
After a medical review the path starts again from the first step. The app never decides the return:
`ready_to_close` is information; closing is the user's.

## Consequences

- Saved format: schema v9 (`injury`, `injury_pain_free`, `injury_checkin`, `injury_sign`), copy
  `backup/ponos-v8-v9.sqlite3`. The plan carries a snapshot without free text (`PlanInjury`), so
  the doctor's notes and the pain scores never go into the saved plan. `METHOD_VERSION` is 4.
- Health data stay in the profile database in clear (the decision recorded in PLAN.md, section 1); never in logs
  or error messages (T-SEC-08 for injuries).
- Open: the 10 km of pain-free walking before running a heel pain is not tracked; steps per zone
  (the days at each step) are project defaults to be tuned with use; the FC at rest, the
  fitness-to-play questions and the OSTRC weekly check are not built.
- Verification: T-INF-01 to T-INF-03, property tests of the path against a naive implementation,
  contract tests of the store on the fake and on SQLite, migration killed at every statement.
