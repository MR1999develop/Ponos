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

## Amendment 2026-11-10: the load is TRIMPeq

This amendment replaces the decision above (hrTSS multiplied by the weight of
the sport). The text above stays as written at the time.

### Context

Fixed sport weights are project choices with no source. They also made an hour
at threshold worth different loads in different sports (strength 1.2, bike
0.8), so the plan's rule "100 = one hour at threshold" held only for running.

### Options

1. **Keep hrTSS with sport weights.**
   - Con: arbitrary numbers; no equivalence between sports.
2. **Raw Banister TRIMP.**
   - Pro: no further parameters.
   - Con: not readable (one hour at threshold is about 170 for one person and
     another value for the next); the plan thresholds would have to be
     converted per person; no equivalence between sports.
3. **Sum of bpm above the minimum, or a ratio on the absolute HR.**
   - Con: load at rest (a resting heart rate would count as effort).
4. **iTRIMP.**
   - Con: needs a laboratory lactate curve.
5. **TRIMPeq: Banister on the running-equivalent intensity.**
   - Pro: keeps Banister's curve (validated on lactate) and the hrTSS scale;
     one hour at the threshold of its own sport is 100 in every sport; no
     weights.
   - Con: the load depends on the running threshold, so an error of 4 bpm
     changes it by about 10%.

### Decision

Option 5. The only measure of load is the TRIMPeq:

- `HRr = (HR - HRmin) / (HRmax - HRmin)`;
  `HRr_T(x) = (threshold_x - HRmin) / (HRmax - HRmin)`;
- `HRr_eq = min(1, HRr * HRr_T(run) / HRr_T(sport))`;
- `w = HRr_eq * 0.64 * e^(k * HRr_eq)`, k = 1.92 for men, 1.67 for women;
- `load = 100 * sum(dt_min * w) / (60 * w(HRr_T(run)))`.

The threshold of each sport comes from `threshold_for_sport`: running and the
sports with no threshold of their own have ratio 1; the bike uses the derived
threshold (running - 4 bpm, or the bike test). For running and the sports with
no threshold of their own the load equals the old hrTSS. At minimum HR the load
is 0; the clamp to 1 applies at maximum HR. The estimate without HR stays
`hours * 100 * IF^2`, marked "estimated". The plan thresholds keep the scale
"100 = one hour at threshold" and need no conversion; `METHOD_VERSION` of the
plan is 7.

### Consequences

- Strength is no longer x1.2 and the alternative sessions are no longer x0.8;
  the bike is evaluated on its own threshold. Frozen bike value in
  `test_fase4_distanza`: 40.1935 (32.18 with the weights).
- The sport weights are removed from the calculation, the settings (page and
  Ctrl+Shift+W), the use cases and the ports. The table `sport_weights` stays
  in the database, neither read nor written (expand-contract: a later
  migration drops it). In `load_cache` the column `hr_tss` receives 0; `trimp`
  and `load` receive the TRIMPeq.
- `LOAD_MODEL_VERSION` is 3 and the fingerprint (`load_fingerprint(physiology)`)
  has no weights: all loads are recalculated from scratch (T-CALC-05).
- Interface labels: "Load (TRIMPeq)", "TRIMPeq per day", "TRIMPeq per week",
  "{load} TRIMPeq".
- Nutrition does not use the load for energy expenditure; it uses it only for
  the day-level thresholds (60/120/200), which stay valid.

### How compliance is verified

- T-CALC-01…07 on the new formulas (`tests/load/test_activity_load.py`,
  `tests/load/test_parameters.py`, `tests/collaudo/test_fase3_carico.py`).
- Equivalence with the previous plans for running only:
  `tests/planning/test_trimp_eq_equivalence.py` compares 11 scenarios with the
  golden file `tests/planning/golden/plans_hrtss.json`.
- Frozen bike value: `tests/collaudo/test_fase4_distanza.py`.

## Amendment 2026-11-10 (later the same day): threshold per sport

The user can set a threshold heart rate for cycling and one for the other
activities (walking, hiking, strength, other) beside the running one. `HRr_eq`
uses the threshold of the sport's group from `threshold_for_sport`: the value
entered, otherwise the default (cycling = running − 4 at least minimum + 1,
other = running). The fingerprint of a cached load includes the threshold of the
group only when it differs from the default, so profiles that never set one
keep their saved loads. Verification: `tests/load/test_sport_thresholds*.py`
and T-CALC-05 for cycling.
