# 004. Load model

- Status: Accepted
- Date: 2026-11-05

## Context

The app computes the load of each activity from heart rate, without power or
pace, and derives ATL, CTL and TSB from it. The plan adapts only to these
values. The formula determines all the numbers shown and all the decisions of
the plan. Changing it after release forces the history to be recalculated.

## Options

1. **hrTSS from Banister's TRIMP on the HR trace.**
   - Pro: uses every sample (not zones), continuous, scales like TSS (100 = one
     hour at threshold), well known in the literature.
   - Con: needs reliable minimum, maximum and threshold HR; the coefficient
     depends on sex; underestimates the load of strength work and of
     activities with unrepresentative HR.
2. **Edwards' zone-based TRIMP.**
   - Pro: simpler to explain.
   - Con: stepwise (threshold effects between zones), less sensitive.
3. **Both selectable.**
   - Pro: comparison between methods.
   - Con: two series of loads and PMC to recalculate, explain and test.
4. **Load computed by Garmin.**
   - Pro: no calculation.
   - Con: opaque formula and not always available; may change without notice;
     does not cover the FIT files imported from other sources.

## Decision

Option 1. The load is the hrTSS multiplied by the weight of the sport.
Options 2 and 3 remain possible as a future addition, with a new ADR.

Parameters:

- minimum and maximum HR entered by the user, with a validity date; a sample
  outside these limits is an error and is discarded (and counted);
- threshold on the bike = running threshold HR − 4 bpm, until there is a bike
  test;
- weights per sport (provisional values, to be tuned in phase 3);
- activities without HR or with too many discarded samples: estimated, marked
  load.

Formulas, units and examples: skill `ponos-allenamento`, file
`references/modello-carico.md`.

## Consequences

- Changing a parameter recalculates the derived loads.
- Wrong user data (maximum HR too low) produces discards: the app reports them
  and suggests reviewing the value.
- The sport weights are project choices, not from the literature, and must be
  declared as such.
- The formulas are in a single domain module.

## How compliance is verified

- T-CALC-01 (hand-computed values), T-CALC-02 (vectorised against naive),
  T-CALC-03 (invariants), T-CALC-04 (PMC), T-CALC-05 (validity date),
  T-CALC-06 (bike threshold and estimates), T-CALC-07 (edge cases).
- Mutation test T-QUAL-01 on `load`.
