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
