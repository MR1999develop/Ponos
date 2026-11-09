# 008. Nutrition model of the day

- Status: Accepted; the food source was amended on 2026-11-09 (see the end)
- Date: 2026-11-07

## Context

Phase 4b adds guidance on food for a past day: energy and macronutrients for
breakfast, lunch, dinner and the intakes around training, with a few food
examples. Four choices are hard to change after release, because the user sees
the numbers and may act on them: what the model computes and what it leaves
out, how the energy of the day is estimated, how far a weight-loss goal may
go, and where the food values come from.

Constraints:

- the app is not a medical device and makes no diagnosis: the numbers are
  general guidance, not personalised medical or nutritional advice;
- the model is pure and deterministic: same data and parameters, same
  guidance, with no I/O, no clock and no randomness in the domain;
- weight, age, heart rate and goals are health data: only in the profile
  database, never in logs, messages or error chains;
- every number comes from a published guideline or is declared a project
  default, and lives in one file, `nutrition/domain/defaults.py`;
- the IOC consensus statement on REDs (2023) was not read for this phase and
  is not a source of any number.

Sources: ACSM, Academy of Nutrition and Dietetics and Dietitians of Canada,
*Nutrition and Athletic Performance* (2016); ISSN position stands (nutrient
timing, protein, diets and body composition, 2017); Mifflin et al. (1990);
Keytel et al. (2005); Boer (1984); *Compendium of Physical Activities*.

## Options

### What the app computes

1. **Targets for the day and per meal, with 2-3 food examples; no diary.**
   - Pro: small surface; the user sees one clear plan per day; no personal
     food data is stored; the domain stays small enough to test fully.
   - Con: the user cannot record what they ate; the app cannot tell how far
     they were from the target; examples are an illustration, not a menu.
2. **Food diary on a complete food database.**
   - Pro: the usual form of a nutrition app; real intake against target.
   - Con: a complete database is large, needs updates and a licence for each
     source; entering meals is a daily burden; it is a different product and
     stores more health data. Rejected by the user on 2026-11-07.
3. **Energy only (kcal), no macronutrients.**
   - Pro: fewer numbers, fewer sources, less risk of a wrong figure.
   - Con: the athlete's main question is carbohydrate around long sessions
     and protein in a deficit; kcal alone answers neither.

### Basal metabolic rate

1. **Mifflin-St Jeor (1990).**
   - Pro: needs only weight, height, age and sex, all in the profile; widely
     used and validated against the older formulas.
   - Con: a population estimate; it ignores body composition and can be off
     by a few hundred kcal for a very lean or very heavy person.
2. **Harris-Benedict.**
   - Pro: familiar.
   - Con: older and less accurate than Mifflin-St Jeor on current
     populations.
3. **Katch-McArdle (from lean mass).**
   - Pro: uses body composition.
   - Con: needs a lean-mass measurement the app does not have; with an
     estimate from weight and height it adds an error without adding
     information.

### Energy outside sport

1. **Factors without sport (sedentary 1.2, light 1.3, active 1.4, very active
   1.5) on the basal rate, plus the net calories of the recorded
   activities.**
   - Pro: the sport the app records is added once, with its real duration and
     heart rate.
   - Con: the factors are project defaults, not values from a paper; the
     level is a self-assessment.
2. **Classic factors (1.375, 1.55, 1.725) that already include exercise.**
   - Pro: values with a long history.
   - Con: with recorded sport added on top, the exercise is counted twice.

### Calories of an activity

1. **Declared by the device if credible; otherwise Keytel (2005) on average
   heart rate, weight, age and sex; otherwise the MET of the sport
   (Compendium).**
   - Pro: uses the best data present; every fallback is stated; the order is
     fixed, so the result is deterministic.
   - Con: a device total can be wrong in a way that still looks plausible;
     Keytel was fitted on a small sample and over- or underestimates outside
     it; a MET is a gross average for a sport.
2. **Always the same estimate (Keytel or MET), ignoring the device.**
   - Pro: one rule.
   - Con: throws away a value the device often measures better, and gives
     different results for the same day depending on the sport.

### Limits of the deficit

1. **A set of independent limits, applied in a fixed order, the most
   restrictive wins.**
   - Pro: each limit has its own source and test; no single formula hides
     the others.
   - Con: for some people one limit (energy availability) always decides and
     the others never act; the number of limits is high.
2. **A single cap on the deficit (for example 500 kcal).**
   - Pro: simple to explain.
   - Con: too much for a small person, too little for a large one, and blind
     to heavy training days and to a low body mass index.

### Food values

1. **A small curated table of common foods (42 entries), values per 100 g
   from the ANSES-CIQUAL table (Etalab 2.0), read from a file shipped with
   the app and checked with `experiments/verify_foods.py`.**
   - Pro: no network call from the app; the licence allows copying and
     redistribution with the citation of the source; a European, official
     reference, with a downloadable release that the script can re-read; the
     table is small enough to verify by hand; examples are reproducible.
   - Con: few foods, no sports drinks, gels or bars yet; names are English
     only until translated; CIQUAL has no entry for some foods (fat-free
     Greek yogurt, part-skim mozzarella, black beans: the closest one is used
     and flagged as approximate in the table); its energy follows EU
     Regulation 1169/2011 (fibre counts), so the Atwater check of a food has a
     wider slack than before (25 kcal).
2. **Queries to a food composition API at run time.**
   - Pro: complete and current.
   - Con: needs the network, possibly a key and a response parser at a new
     trust boundary; guidance would change with the source data; nothing
     works offline.
3. **USDA FoodData Central (CC0), the first choice.**
   - Pro: public domain, no obligation.
   - Con: American foods and preparations; replaced on 2026-11-09 (see the
     amendment at the end): the owner asked for European references, and
     about ten of the FoodData Central ids written without access to the
     database pointed at the wrong food.

## Decision

- **Scope:** option 1 of "What the app computes". No diary, no allergy or diet
  restrictions for now. A fixed notice states that the guidance is general
  and not medical, and invites to consult a professional with illness,
  pregnancy, eating disorders or medicines.
- **Rest rate:** Mifflin-St Jeor, in years completed on the day.
- **Expenditure** = rest rate x factor without sport + net calories of the
  activities. Net = total minus the rest rate for the duration, never below
  zero. Without a level set, sedentary, with a warning.
- **Activity calories:** declared by the device when at most 1,500 kcal per
  hour of moving time; a higher figure is discarded, never clipped, and
  estimated as if absent. Then Keytel, then the MET of the sport.
- **Deficit.** Pace from 100 to 1,000 g per week, cut to 1% of body weight per
  week (ACSM). Limits, in this order: no deficit with a body mass index under
  18.5; at most 20% of the expenditure and 750 kcal; reduced on heavy days (x
  0.5 high, x 0.25 very high); intake never under the larger of the rest rate
  and 1,200 kcal (women) or 1,500 kcal (men); energy availability never under
  30 kcal per kg of lean mass per day, with lean mass estimated by Boer
  (1984). Under 18 years the model gives no numbers at all.
- **Macronutrients.** Carbohydrate in g/kg by day level (rest and light 3-5,
  moderate 5-7, high 6-10, very high 8-12); protein 1.6 g/kg (2.0 in
  deficit); fat the rest, at least 20% of energy. If energy is short,
  carbohydrate gives way.
- **Moments.** Before (session over 60 min): 1 g/kg carbohydrate. During (over
  75 min): 45 g/h, 60 g/h beyond 2.5 h. The ceiling is 60 g/h because the
  table does not tell glucose-fructose blends apart; higher figures need
  them. After: 0.4 g/kg protein and, for a session of at least 90 min, 1.1 g/kg
  carbohydrate. With another session less than 8 hours later (rapid
  refuelling), 1.1 g/kg carbohydrate an hour for the first 4 hours, served as
  two intakes two hours apart that merge with a close meal (user choice of
  2026-11-09: one intake an hour made the day unmanageable).
- **Foods.** The table of option 1; its source and licence are in
  `docs/dipendenze.md` (ANSES-CIQUAL since the amendment). The release is
  blocked while `verified_on` is empty.
- **Constants.** Every threshold, factor and coefficient is in
  `nutrition/domain/defaults.py` with its source or the words "project
  default". The detail is in the project skill, `references/alimentazione.md`.

## Consequences

- The numbers can be wrong for an individual: a population formula, a
  self-assessed level, a device total. The fixed notice and the limits
  reduce the harm; they do not remove it.
- A change of any constant changes the guidance of every past day. The change
  goes through `defaults.py`, the T-NUT tests and the skill reference, in one
  modification; nothing is stored, so no migration is needed.
- Schema v4 adds `activities.calories_kcal`, the activity level, the meal
  times and the goal periods (migration with a safety copy). Activities
  indexed before v4 have no stored calories; the day reads them from the
  original file. The goal periods are kept apart by triggers on `INSERT` and
  `UPDATE`, both part of v4: v4 was changed before any release, so a
  database at v4 without the `UPDATE` trigger can only be a development copy.
- Food values are a trust boundary: the table is parsed with size, depth and
  value checks (threat model, D-F4B).
- Future days (phase 5) reuse the same model. A rule about racing weeks needs
  a new ADR, not a change of this one.
- Sports drinks, gels and bars cannot be suggested until verified entries
  exist; for long sessions the examples use fruit and dried fruit only.
- This ADR is not changed after acceptance. A different model replaces it
  with a new ADR.

## How compliance is verified

- T-NUT-01: rest rate, expenditure, deficit and macros against hand-computed
  values for a man and a woman, at the limits of age, weight and level
  (`tests/nutrition/`, `tests/collaudo/test_fase4b_modello.py`).
- T-NUT-02: deficit never over 20% or 750 kcal, never under the rest rate or
  the minimum intake, never under the energy availability floor; none under
  18 years or with a low body mass index; property test on random people and
  days (`tests/collaudo/test_fase4b_modello.py`).
- T-NUT-03: activity calories from the device, Keytel and MET; the rest rate
  is not counted twice (`tests/collaudo/test_fase4b_giorni.py`).
- T-NUT-04: moments and meals, including sessions at dawn, at noon, late, two
  in a day and a long one; no negative quantity; the sum of the meals is the
  total of the day.
- T-NUT-05: determinism, and dated changes of weight, level and goal valid
  from their day only.
- T-NUT-06: food examples within the declared tolerance; the shipped table
  declares source and licence (`tests/collaudo/test_fase4b_alimenti.py`); the
  source is in the dependency register (D-F4B-08).
- All constants in one file: checked in code review; no automatic test yet.
- `import-linter` in the CI: the nutrition domain stays pure.
- Before the release: `experiments/verify_foods.py` has run on the table and
  `verified_on` holds the date (`tests/nutrition/test_foods.py` checks that
  the shipped file says it was verified).

## Amendment 2026-11-09: food source

Only the food table changes; the model above does not.

- **Decision:** the values of `foods.json` come from the ANSES-CIQUAL 2025
  table (Etalab 2.0), identified by `alim_code`, instead of USDA FoodData
  Central. Format of the file: version 2 (`ciqual_code`, `ciqual_name`, optional
  `ciqual_note` per food; `source.version`).
- **Why:** the owner asked for European references (CIQUAL and CREA), and about
  ten of the FoodData Central ids of the first table, written without access to
  the database, pointed at the wrong food: the table could not be verified as
  it was.
- **CREA** (the Italian tables) is not copied: its site forbids reproducing data
  without a clear citation and offers no bulk download or open licence. It is
  only compared, entry by entry, by `experiments/verify_foods.py --crea-fetch`
  (report, not a failure).
- **Consequences:** the table is verified against a release that can be
  downloaded again (`--ciqual-dir`); three foods are flagged as approximate;
  the check of a food's energy against its macros allows 25 kcal (the EU
  labelling counts fibre) and the macros of a food may add up to 100.5 g
  (measured values); with the new values a meal of 175 g of carbohydrates or
  more during a session, and one breakfast of 89 g carbohydrates and 49 g
  protein, get no example (the examples are only an illustration; the grams of
  the meal are not affected).
- **How it is verified:** as above, plus `tests/experiments/test_verify_foods.py`
  (reading of the release, DTD and size refusal, rule for traces and bounds,
  round trip of the shipped file).
