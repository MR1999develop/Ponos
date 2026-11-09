# Experiments

`garmin_spike.py` is the phase 0 spike: it checks live access to Garmin
Connect with `garminconnect` (login with MFA, tokens in the keyring, latest
activities, count and timings of the history, one original FIT download with
track reading, scheduled workouts read-only).

- Launch, in your own terminal (it asks for email, password and possibly the MFA
  code; none of the three is saved): `uv run --locked python experiments/garmin_spike.py`.
  `--downloads N` downloads up to 5 files for the time statistic.
- It writes `experiments/garmin_spike.md` (non-personal data only),
  `experiments/garmin_spike_shapes.json` (anonymous structure of the real responses:
  one element of the activity list, count response, scheduled
  workouts, tokens JSON; only key names, types, lengths and orders of
  magnitude, never values; it serves to write fixtures for the contract tests) and, in
  `experiments/spike-data/` (ignored by git), the downloaded zip and FIT.
  In the keyring it puts the `PonosCoach-spike` entry.
- Warning: `experiments/spike-data/` contains HR and GPS in the clear; at the end of the test
  it must be deleted with `--logout`.
- Cleanup: `uv run --locked python experiments/garmin_spike.py --logout` deletes the keyring
  entry and the files in `spike-data/`.
- Exits with 0 if all the steps succeed, with 1 if any fails.
- The report also gives the token size in UTF-8 bytes, characters and UTF-16
  bytes and whether it exceeds 2560 bytes (limit of the generic Credential Manager).
- `spike_shapes.py` contains `shape_of`, the pure function that anonymises; it is tested
  in `tests/experiments/test_spike_shapes.py`.

## verify_foods.py

- `uv run --locked python experiments/verify_foods.py --ciqual-dir DIR [--apply [--force]]
  [--crea-dir DIR | --crea-fetch]` checks `src/ponos_coach/nutrition/foods.json` against the
  ANSES-CIQUAL release in `DIR` (`alim_YYYY_MM_DD.xml` and `compo_YYYY_MM_DD.xml`, downloaded
  by hand from recherche.data.gouv.fr, DOI 10.57745/RDMHWY; keep them outside the repository).
  The files are untrusted: size-capped, refused if they declare a DTD, streamed.
- It prints, per food, the difference from CIQUAL in percent for each value and flags a missing
  code, a stored original name that is not the entry's and differences over 10% and over
  0.5 g or 5 kcal. Rule for the values: `traces` is 0, `< x` is x/2, missing is never repaired.
- With `--apply` it rewrites values, original names and `source.version` from CIQUAL and sets
  `source.verified_on` to today, only if every code was found (or with `--force`); the new file
  must pass the table's parser (atomic write).
- CREA (`alimentinutrizione.it`) is compared, never copied: `experiments/crea_mapping.json` has the
  CREA code and title of each food; `--crea-fetch` reads the pages (one request per second) and
  `--crea-dir` reads pages saved as `<code>.html`. The report lists the differences; it never
  changes the exit status.
- Exit status: 0 fine, 1 input or network error (no file touched), 2 the table has problems.
  Tested in `tests/experiments/test_verify_foods.py` (synthetic files, no network).
