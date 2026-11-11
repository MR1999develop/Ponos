# 007. Profile data, dated measurements, sport weights and HR zones

- Status: Accepted
- Date: 2026-11-06

## Context

Phase 3 adds height, date of birth and weight to the profile, makes minimum HR,
maximum HR and threshold dated too, lets the user choose the sport weights and
computes the time in HR zones. Five choices stay in the user's files or in the
numbers they see, and are hard to change after release: which data is fixed and
which is dated, how long a measurement is valid, how the weight is saved, how
the sport weights are saved and when a load must be recalculated, how the zones
are defined. A sixth concerns the interface language.

This ADR adds schema version v3 to ADR 005 and refines ADR 004 (load model).
The rest of both remains valid.

Constraints:

- height, weight and date of birth are health data: only in the profile
  database, never in logs, messages or error chains;
- a profile created before v3 stays valid and loses nothing;
- the database is an index and the user's data; loads and zones are derived and
  are regenerated from the original files;
- a change of parameters must not leave old loads mixed with new ones;
- the app is not a medical device: the zones are a training indication.

## Options

### Fixed data and dated data

1. **Fixed: height and date of birth. Dated: weight, minimum HR, maximum HR,
   threshold.**
   - Pro: what does not change over time has no history; what changes has one,
     and the load of an activity uses the values of its day. A trigger makes
     the fixed data immutable even against a bug in the code.
   - Con: a typing error in height or date of birth cannot be corrected from
     the app; the distinction must be explained to the user.
2. **Everything dated, including height and date of birth.**
   - Pro: a single rule; an error is corrected with a new entry.
   - Con: a history that makes no sense for the date of birth; more rows to
     validate and show; changing it would change the age in the past.
3. **A modifiable profile row, without history.**
   - Pro: minimal schema.
   - Con: a change of HR or threshold would rewrite the past: the load of an
     activity from three years ago would be recalculated with today's values.

### How long a measurement is valid

1. **A measurement is valid from its day until the next entry of the same
   series; weight and the HR parameters are two series. Same day: the entry is
   replaced. Values not indicated stay those in force that day.**
   - Pro: inserting a past measurement does not change what is valid after a
     more recent entry; it is the rule that whoever looks at the history
     expects; the use case returns the effective period and the interface shows
     it.
   - Con: a measurement with a past date may be valid only for a few days, and
     the user who enters it may expect it to be valid "from then on": for this
     reason "Edit profile" shows the effective period and warns when it ends
     before the present.
2. **A measurement is valid from that day on and replaces the later entries.**
   - Pro: the effect is what the user writes.
   - Con: it deletes real data without saying so; against the rule "the user's
     data is not overwritten".
3. **Only the last measurement entered is valid, for the whole history.**
   - Pro: no period to show.
   - Con: like option 3 of fixed and dated data, it rewrites the past.

### How the weight is saved

1. **Integer in tenths of a kilogram (`tenths_kg`), from 20.0 to 300.0 kg;
   single decimal digit; text read with a strict grammar (ASCII digits, comma
   or point).**
   - Pro: no binary floating-point comparison; the database limits it with a
     `CHECK` on integers; `70,5` and `70.5` are the same value.
   - Con: a scale that gives two decimals is not accepted (`70,55` is rejected,
     not rounded); the conversion is in a single place.
2. **`REAL` in kilograms.**
   - Pro: no conversion.
   - Con: `70,1` is not exact in binary; comparisons and equality of entries
     become fragile.
3. **Text with the decimal number (`Decimal`).**
   - Pro: exact.
   - Con: the database cannot limit it with a numeric `CHECK`; a `STRICT` text
     type accepts any string.

### Sport weights and when a load is recalculated

1. **Table `sport_weights` with only the weights different from the default;
   the fingerprint of the load of an activity includes only the weight of its
   sport.**
   - Pro: changing the project default in a future version reaches whoever
     never touched it; changing the bike weight does not recalculate the runs (a
     recalculation re-reads the files: with 5,000 activities it is long work).
   - Con: the saved set depends on the defaults in the code: a changed default
     also changes the load, and `LOAD_MODEL_VERSION` must be raised when all
     loads are to be recalculated.
2. **Table with the weights of all sports.**
   - Pro: everything that is used is in the database.
   - Con: the defaults become user data: a better default no longer reaches
     anyone without a migration.
3. **Single fingerprint with all the weights.**
   - Pro: simpler to compute.
   - Con: changing one weight invalidates every load of every sport.

### HR zones

> **Amendment of 2026-11-11.** The Z1/Z2 boundary moves from 75% to 72% (user's
> request): Z1 50-71%, Z2 72-84%; the other boundaries stay. `METHOD_VERSION` 11.
>
> **Amendment of 2026-11-10.** The percentages are the user's choice: Z1 below
> 75% (50-74%, and everything lower), Z2 75-84%, Z3 85-94%, Z4 95-102%, Z5 from
> 103% to the maximum HR (the 5th edition's 78/87/94/100 are superseded). Same
> design: the percentages live only in `load/domain/zones.py`, the load
> fingerprint includes them (the seconds in zone are recomputed, the TRIMPeq does
> not change) and the plan is rebuilt (`METHOD_VERSION` 10). The text of option 1
> below keeps the original numbers as history.

1. **Five zones on the threshold of the sport, after Friel: Z1 below 85%, Z2
   85-89%, Z3 90-94%, Z4 95-99%, Z5 from 100%. Project default, to be reviewed
   in phase 4 with the threshold test. The percentages are only in
   `load/domain/zones.py`; the seconds in zone are saved with the load.**
   - Pro: the threshold is already the parameter of the load (hrTSS); on the
     bike the bike threshold applies; no extra data to ask for.
   - Con: the threshold entered before the phase 4 test may be an estimate (90%
     of the maximum at creation), and so the zones; the percentages come from a
     single source and do not adapt to people with a different profile.
2. **Percentages of the reserve HR (Karvonen).**
   - Pro: takes the resting HR into account.
   - Con: a second set of parameters to explain; it does not match the
     threshold used by the load.
3. **Percentages of the maximum HR.**
   - Pro: no threshold needed.
   - Con: the maximum HR is the least reliable of the values entered; the zones
     would not follow the threshold test.
4. **Zones configurable by the user.**
   - Pro: suitable for whoever already has their own.
   - Con: more interface and more input cases to validate; postponed until
     needed (YAGNI).

### Schema v3

1. **Additive migration: tables `profile_body`, `weight`, `sport_weights`; five
   columns `zone1_s ... zone5_s` in `load_cache`; the cache is emptied in the
   same migration. A trigger rejects every `UPDATE` and `DELETE` of
   `profile_body`.**
   - Pro: the cache is derived: emptying it guarantees that no load is left
     without zones and costs only a recalculation; the `CHECK` constraints are
     in the database; the migration goes through the safety copy like the
     others.
   - Con: the first start after the update recalculates all the loads (with
     5,000 activities about 23 s with real FIT files, estimated); the trigger
     also prevents future code from correcting the fixed data, which would
     require a new migration.
2. **Zone values in a separate table.**
   - Pro: `load_cache` does not change.
   - Con: one more table and foreign key for data that is born and dies with
     the load.
3. **Zones computed on the fly from the files.**
   - Pro: no extra data.
   - Con: the time-in-zones chart would re-read all the files at every opening.

### Interface language

1. **English by default; Italian is chosen from the Language menu and the
   choice is remembered for the next start; `--lang` takes precedence.**
   - Pro: no dependence on the system language, so same behaviour everywhere and
     in tests; an English source text does not need a catalogue.
   - Con: whoever uses Italian must choose the language once; the change
     applies at restart.
2. **System language.**
   - Pro: no choice for the user.
   - Con: depends on the environment, hard to test; an untranslated system
     language falls back in an unclear way.

## Decision

- **Fixed data:** height (integer, 100-250 cm) and date of birth (from
  1900-01-01 to today minus 5 years, upper limit checked when the user enters
  it). They are written once (`FixedDataStore.set_once`); a profile created
  before v3 completes them once in "Edit profile".
- **Dated measurements:** weight (tenths of a kilogram, 20.0-300.0 kg), minimum
  HR, maximum HR, threshold. A validity day not in the future and not before
  1900-01-01. The constraint `minimum HR < threshold <= maximum HR` is checked
  on the result. A change from "Edit profile" is a single use case and a single
  transaction, fixed data included.
- **Validity:** the rule of option 1 applies; the use case returns the
  effective period (`ValidityPeriod`) and the interface shows it and warns if
  the past date is valid only until the next measurement. Body weight does not
  enter the load.
- **Sport weights:** from 0 to 5, two decimals; only those different from the
  default are saved; the fingerprint of a load includes the weight of the sport
  alone, the zone percentages and `LOAD_MODEL_VERSION` (2 since phase 3).
- **Zones:** option 1. The seconds in zone are those of the samples counted by
  the load (same discards and same gaps). A sample on the boundary belongs to
  the higher zone; the comparison is on integers.
- **Schema v3:** option 1.
- **Language:** option 1.
- **Personal data:** the domain types and the stores do not repeat rejected
  values in messages; none of these values enters a log.

## Consequences

- An error in height or date of birth cannot be corrected from the app. If the
  request becomes frequent, a new ADR decides (this one is not modified).
- The zone percentages are provisional: changing them in phase 4 requires a new
  version of the load model, so a recalculation of all the loads, and an ADR
  that replaces the part on zones.
- The first start after the update to v3 recalculates the loads with progress
  and Stop.
- The defaults of the sport weights are part of the visible behaviour: changing
  them without raising `LOAD_MODEL_VERSION` leaves old loads in the cache.
- Body weight has a history but no use in the calculations; it is ready for
  later phases without further migrations.
- Every change of the set of fixed data requires a migration, a safety copy and
  a test on the database of the previous version (as for every schema, ADR 005).

## How compliance is verified

- T-DATI-01: v2 database written by hand brought to v3 with data intact and a
  consistent safety copy; migration interrupted at every statement; limits of
  the `CHECK`s, trigger of the fixed data
  (`tests/collaudo/test_fase3_migrazione.py`,
  `tests/collaudo/test_fase3_dati_personali.py`); two instances and double
  completion of the fixed data with a single winner.
- T-QUAL-02: partitions and boundaries of height, date of birth, weight, HR
  triples, sport weights and validity day, also through the use case and the
  database (`tests/collaudo/test_fase3_dati_personali.py`).
- Validity periods per series: `tests/profiles/test_measurements.py`.
- T-CALC-01…07: comparison between the incremental overview after a series of
  changes and the calculation from scratch; the fingerprint depends on every
  input of the load and on nothing else; zones with an oracle written
  separately (`tests/collaudo/test_fase3_carico.py`,
  `tests/load/test_parameters.py`, `tests/load/test_sport_weights.py`).
- T-SEC-08: no height, weight, date of birth or HR value in logs, messages or
  error chains (`tests/collaudo/test_fase3_log_salute.py`).
- T-UI-06 and T-UI-07: same texts in the two languages, language remembered
  (`tests/ui/test_translations.py`,
  `tests/collaudo/test_fase3_interfaccia.py`).
- T-PERF-01: overview with 5,000 activities, full recalculation and charts
  within budget (`tests/collaudo/test_fase3_prestazioni.py`).
- `import-linter` in the CI: the domain stays pure.

## Amendment 2026-11-10: sport weights and hrTSS removed

The load is now the TRIMPeq (amendment to ADR 004). The parts of this ADR on
the sport weights (options, decision, consequences and the tests
`tests/load/test_sport_weights.py`) no longer apply, and the references to hrTSS
mean the TRIMPeq. The rest of the ADR stays valid.

- The table `sport_weights` stays in the schema, neither read nor written; a
  later migration drops it (expand-contract).
- `LOAD_MODEL_VERSION` is 3 and the fingerprint of a load, `load_fingerprint(physiology)`,
  has no weights: it depends on the HR parameters and the zone percentages.
  Everything is recalculated from scratch (T-CALC-05).
- The zones keep their definition on the threshold of the sport.

## Amendment 2026-11-10: thresholds for cycling and other activities

Migration 15 (`SPORT_THRESHOLDS_MIGRATION`, `storage/schema_sport_thresholds.py`)
adds the nullable columns `bike_threshold_bpm` and `other_threshold_bpm` to
`physiology`, with `CHECK (>= 40 AND > min_bpm AND <= max_bpm)`; NULL means the
default. They share the dated series of the other HR parameters: one entry per
day, one row in the history, one constraint. The HR zones of a sport are on its
threshold.

## Amendment 2026-11-10 (second): LT1 and the two groups

Migration 18 (`LT1_MIGRATION`, `storage/schema_lt1.py`) adds the nullable columns
`run_lt1_bpm` and `other_lt1_bpm` to `physiology`, with
`CHECK (NULL OR (> min_bpm AND < LT2))`; for `other_lt1_bpm` the LT2 is
`COALESCE(other_threshold_bpm, threshold_bpm)`. The column `bike_threshold_bpm`
of migration 15 stays in the table but is neither read nor written any more,
because cycling belongs to the other-sports group (expand-contract: a later
migration drops it). `other_threshold_bpm` is the LT2 of every sport that is not
running or trail. The load cache is not cleared: `LOAD_MODEL_VERSION` 5 changes
the fingerprint of every load.
