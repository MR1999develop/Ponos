# 011. Nutrition for the days ahead

- Status: Accepted
- Date: 2026-11-07

## Context

ADR 008 gives the nutrition model of a past day and says that days ahead reuse
it and that a rule about racing weeks needs a new ADR. Phase 5 adds the saved
plan, so a day after today can have guidance: the activities are the sessions
the plan has for it. Two choices are hard to change once the user acts on the
numbers: where the activities of a day ahead come from, and what the model does
with a weight-loss goal around races.

Constraints are those of ADR 008: not a medical device, pure and deterministic
model, health data only in the profile database, constants in
`nutrition/domain/defaults.py` or declared in the code that uses them.

Source: ACSM, Academy of Nutrition and Dietetics and Dietitians of Canada,
*Nutrition and Athletic Performance* (2016): lose weight away from the
competitive season; low energy availability harms performance and recovery.

## Options

### Activities of a day ahead

1. **The planned sessions (and the race) of the day, at a typical time, with
   the energy of the sport's METs.**
   - Pro: same model as a past day; deterministic; nothing recorded is needed.
   - Con: an estimate: no heart rate, the plan may change, the start time is a
     guess; the METs are one value per sport, not per zone.
2. **No guidance for days ahead.**
   - Pro: nothing to get wrong.
   - Con: the athlete needs the plan's demanding days most to prepare food.

### Start time of a planned session

1. **Mean start of the sessions of the last 60 days on the same weekday;
   18:00 where there are none.**
   - Pro: follows the user's habit; simple to explain.
   - Con: a mean of very different times is a time the user never trains.
2. **Always 18:00.** Pro: one rule. Con: ignores the habit.

### Deficit around races

1. **No deficit in the Monday-to-Sunday week of an A or B race and in the days
   of final taper (A) or mini taper (B) that precede it.** The day gets the
   advisory `DEFICIT_PAUSED_FOR_RACE` when the goal is weight loss.
   - Pro: one rule from the plan's own phases; the goal stays what the user
     chose, only the day changes.
   - Con: the week after a race (recovery) is paused only while it is the same
     calendar week; the rest of the recovery keeps the deficit.
2. **Keep the deficit, only warn.** Pro: no change of the user's goal.
   Con: the model would suggest a deficit just before a race.

## Decision

- Option 1 for the activities, with the start time of option 1 (constants
  `TYPICAL_TIME_LOOKBACK_DAYS` = 60 and `DEFAULT_TRAINING_MINUTE` = 18:00 in
  `profiles/day_nutrition.py`: project defaults). A race is a session of its
  day with the time the plan expects. Weight, level, lean mass and goal are
  those valid on the day (the last value); the result says it is an estimate
  from the plan (`DayNutritionReport.from_plan`).
- Without a saved plan, with a refusal, or for a day the plan does not reach,
  `NutritionUnavailable(NO_PLAN_FOR_DAY)`. The age checks come first, as for
  every day.
- Option 1 for the deficit. `DayInput.deficit_paused` makes the model treat a
  weight-loss goal as maintenance for the energy and the macros, and adds the
  advisory; a C race pauses nothing. The rule lives in the use case, which
  reads the plan; the nutrition domain only receives the flag.

## Consequences

- A change of the plan changes the guidance of the days ahead; nothing is
  stored, so no migration is needed.
- Days up to today are unchanged.
- The week and taper rule changes the numbers of every plan with an A or B
  race; changing it means a new ADR.

## How compliance is verified

- T-NUT-07: a day ahead has the planned session and the race, the typical
  time and the default one, the MET energy, the intakes around training, no
  guidance without a plan or beyond it, determinism
  (`tests/profiles/test_day_nutrition_future.py`).
- T-NUT-08: no deficit in the taper, in the race week (also after the race)
  and in the mini taper of a B race; a deficit again the week after and before
  the week; no pause for a C race or without a weight-loss goal; property test
  on the pure model (`tests/nutrition/test_paused_deficit.py`).
- T-UI: the page of a day ahead shows the note and the advisory, the missing
  plan has its own text (`tests/ui/test_day_window_future.py`).
