# Acceptance testing: results

Register of the results of the acceptance testing described in section 7 of
`PLAN.md`. Every row cites the test identifier.

## How to fill it in

- One row for each run, never rewritten: a new result is a new row.
- Date in the format YYYY-MM-DD.
- Platform: Windows or Ubuntu, with the system version.
- Version: that of the executable tested.
- Result: passed, failed, passed with reservations.
- Notes: problems found, reference to the fix, measurements (with
  environment).

## Long campaign (`uv run --locked nox -s collaudo`)

| Test | Date | Platform | Version | Result | Notes |
|---|---|---|---|---|---|
| Phase 1, `tests/collaudo/` (83 tests) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-1-profili-archivio`, from source | passed | Defects D-F1-01…10 found on 2026-11-05 and fixed (table of closed cases in `docs/threat-model.md`). Symlink, FIFO and POSIX permissions skipped on Windows: to run in CI on Ubuntu. |
| T-PERF-01 (partial) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-1-profili-archivio`, from source | passed | Import of 1,000 synthetic FIT files: 14.0 s; per file p50 14 ms, p95 15 ms, max 19 ms. Cold overview 10.1 s; warm p50 28 ms, p95 29 ms. |
| T-UI-02 / D-F1-10 | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-1-profili-archivio`, from source | passed with reservations | UI thread stall during import and overview: p95 ≤ 15 ms; first drawing 21–25 ms. The web engine start (about 180 ms) stays in the UI thread right after the window opens: see `TIDY.md`. |
| Phase 2, `tests/collaudo/test_fase2_*` and T-SYNC-02 (144 tests with phase 1) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-2-sincronizzazione`, from source | passed | Defects D-F2-01…04 found and fixed (table of closed cases in `docs/threat-model.md`). Fake Garmin clients only: the real login is left for T-MAN-01. |
| T-SYNC-01 on 20,000 activities | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-2-sincronizzazione`, from source | passed | Listing p50 2.3 ms, max 25 ms per page; matching p50 0.39 ms, max 2.9 ms per entry; full run 8.7 s with a fake source. |
| T-UI-02 during synchronisation | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-2-sincronizzazione`, from source | passed | 120 real FIT files downloaded from a fake source: UI thread stall p50 9 ms, max 14 ms. |
| Phase 3, `tests/collaudo/test_fase3_*` (149 acceptance tests) | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-3-carico-e-grafici`, from source | passed with reservations | Defects D-F3-02…11 found (table of closed cases in `docs/threat-model.md`); the defect tests start as expected-fail: when they pass without the mark, a new row with the result is added. Covers T-CALC-01…07, T-DATI-01 (migration v3), T-QUAL-02, T-SEC-08 (personal data), T-UI-04…07. No real login. |
| T-PERF-01 with 5,000 activities | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-3-carico-e-grafici`, from source | passed | Overview from the cache p50 141 ms; PMC figures 127 ms; weekly load 17 ms; zones 17 ms. PMC redraw at 3,650 days 720 ms in the software rendering used for tests. Recalculation from scratch estimated at about 23 s with real FIT files (estimate from the decoding cost of one real FIT, not a measure of the full run). Synthetic activities, except decoding. |
| Phase 3, `tests/collaudo/test_fase3_*` after the fixes | 2026-11-06 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-3-carico-e-grafici`, from source | passed | D-F3-01…11 fixed, no `xfail` left; `nox` with 3398 tests and acceptance with 149 tests green. |
| Phase 4b, `tests/collaudo/test_fase4b_*` (model, days, foods, migration, personal data in logs, interface) | 2026-11-07 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-4b-alimentazione`, from source | passed | Defects D-F4B-01…09 found and fixed (table of closed cases in `docs/threat-model.md`). Covers T-NUT-01…06, T-DATI-01 (migration v4), T-SEC-08 (nutrition data), T-UI-01, T-UI-06, T-UI-07. No `xfail` left. |
| Phase 4b, full run | 2026-11-07 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-4b-alimentazione`, from source | passed with reservations | `uv run --locked nox` green with 4,800 tests; `uv run --locked nox -s collaudo` green with 152 tests. Reservation: the food values are not verified against FoodData Central (`verified_on` empty); `experiments/verify_foods.py` must run before the release. No real Garmin login. |

## Executable acceptance testing

| Test | Date | Platform | Version | Result | Notes |
|---|---|---|---|---|---|
| | | | | | |

## T-MAN-01: manual acceptance on clean systems

Clean Windows and Ubuntu, from the executable. The user does the login with
MFA.

| Step | Date | Platform | Version | Result | Notes |
|---|---|---|---|---|---|
| Guided first start | | | | | |
| Real login with MFA | | | | | |
| Real first synchronisation | | | | | |
| Import from a real folder | | | | | |
| Plan towards a real goal | | | | | |
| Sending a workout to the Garmin calendar | | | | | |
| Deleting a workout from the Garmin calendar | | | | | |

## T-MAN-02: usability test

2–3 people, precise tasks. No names: only the participants are recorded as
"P1", "P2", "P3".

| Task | Participant | Date | Succeeded | Problem observed | Fix |
|---|---|---|---|---|---|
| Create a profile | | | | | |
| Add a race | | | | | |
| Read today's form | | | | | |
| Describe an injury | | | | | |
