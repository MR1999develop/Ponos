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
| Phase 4, `tests/collaudo/test_fase4_*` (end-to-end test, distance, apply and undo with process deaths, retest and time zones, migration v5, health data in logs, fuzzing, performance) | 2026-11-07 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-4-test-soglia`, from source | passed | Defects D-F4-01…06 found and fixed (table in `docs/threat-model.md`), no `xfail` left. Covers T-PIANO-06, T-DATI-01 (migration v5), T-SEC-02 (distance fuzzing, 5,000 examples), T-SEC-08. Load and cache fingerprint identical to phase 4b on the same files. |
| T-PERF threshold test evaluation | 2026-11-07 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-4-test-soglia`, from source | passed | 6 h at 1 Hz (21,601 records): evaluation p50 1.4 ms, p95 1.8 ms; decoding plus evaluation p50 362 ms; 24 h 8.9 ms; 1,000,000 records 108.5 ms (growth below quadratic). 15 repetitions. |
| Phase 4, full run | 2026-11-07 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-4-test-soglia`, from source | passed | `uv run --locked nox` green with 5,199 tests; `uv run --locked nox -s collaudo` green with 164 tests. No real Garmin login. |
| Phase 5, `tests/collaudo/test_fase5_*` (plan end to end, properties, hand-edited plan, undo and interruptions, 20-minute protocol, zones of the 5th edition, measured lean mass, nutrition of the days ahead, refuelling times, health data in logs, migrations, performance) | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Defects D-F5-01…04 found and fixed (table in `docs/threat-model.md`; rows of D-F5-02 and D-F5-04 still to be written). Covers T-PIANO-01…06, T-NUT-04, T-NUT-07, T-NUT-08, T-DATI-01, T-DATI-02, T-SEC-02 (saved plan), T-SEC-08. |
| T-PERF plan | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Plan of 52 weeks with four goals: about 10 ms. Nutrition of a day ahead: 9 ms. |
| Phase 5b, `tests/collaudo/test_fase5b_*` (map channel, tiles and cache, tracks and areas, migration v8) | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Defects D-F5B-01…03 found and fixed (table in `docs/threat-model.md`). Covers T-SEC-14, T-SEC-15, T-MAP-03, T-MAP-05, T-MAP-07, T-MAP-10, T-MAP-11, T-DATI-01 (migration v8). The tile server is a local one that plays hostile answers; no real call to Tracestrack. |
| T-MAP-05 with 50,000 activities | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Area list p95 5.1 ms; move p95 19.5 ms (budget 400 ms). Synthetic activities and tracks. |
| Phase 6, `tests/collaudo/test_fase6_*` (path by steps, plan, undo, migration v10, health data in logs) | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Defect D-F6-01 found and fixed. Covers T-INF-01, T-INF-02, T-INF-03, T-DATI-01 (migration v10), T-SEC-08 (injuries). |
| Phase 7, `tests/collaudo/test_fase7_calendario.py` (send, deletion, copies, hostile responses, lost answers) | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed with reservations | Defects D-F7-01…03 found and fixed. Covers T-SEC-10, T-SYNC-03, T-SYNC-04 for the calendar. Fake Garmin calendar only: the shape of the real responses and the real send and deletion are left for T-MAN-01. |
| Phase 8, `tests/collaudo/test_fase8_*` (backup, restore, hostile ZIPs, interruptions, package) | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source and from the built archive | passed | Defects D-F8-01…04 found and fixed. Covers T-DATI-05, T-DATI-02, T-SEC-02, T-SEC-07 (no token in the backup), T-SEC-12 (package). |
| T-PERF backup and restore | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | Profile with 1,000 activity files: backup 6.25 s, restore 1.18 s. |
| Package size | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, `nox -s dist` | passed | Windows package: 245 MB (zip). |
| Phases 5 to 8, full run | 2026-11-08 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed after fixes | `uv run --locked nox`: 8,097 tests passed, 2 Italian-label tests and one mypy finding on Linux fixed afterwards; `uv run --locked nox -s collaudo`: 179 passed, 2 property-test counterexamples (wrong invariants, not code defects) fixed with regression tests. The fixed tests and `nox -s types` and `nox -s i18n` were rerun green; the whole suite was not rerun after these test-only changes. No real Garmin or Tracestrack calls. |
| Pre-release, `tests/collaudo/` (2,725 tests: phases 1 to 8 and the new `test_fase9_*` on the work since 2026-11-08: deleted activities, sport thresholds and zones, race ascent, map backgrounds and certificates, entries Garmin never serves, activity detail charts, active calories, health data in logs) | 2026-11-11 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed | No defect found by the new tests. Covers T-DATI-04, T-DATI-07, T-SYNC-07, T-SYNC-08, T-CALC, T-PIANO-11, T-MAP-12, T-SEC-08, T-SEC-14, T-SEC-15, T-UI-09…11, T-NUT-09. Local TLS server with a throwaway self-signed certificate; no real Garmin or tile server. |
| Pre-release, long campaign (`-m collaudo`, 191 tests) | 2026-11-11 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed after fixes | First run: T-UI-02 failed because its probe used the user's map cache folder and the window did not open when that cache could not be opened (D-F9-04, D-F9-05, fixed). Second run: one property-test counterexample (a span of 5e-324, a subnormal float that is no chart value: wrong invariant, not a code defect), fixed by narrowing the strategy and rerun green. The whole campaign was not rerun after that test-only change. |
| T-UI-02 after D-F9-04 | 2026-11-11 | Windows 10 Pro 22H2, 12 CPUs | branch `fase-5-piano`, from source | passed with reservations | 300 real FIT files imported from the window: event loop intervals p95 14 ms, max 26 ms during the import; p95 13 ms, max 16 ms during the overview. The web engine start still holds the UI thread for about 200 ms (see `TIDY.md`). |
| Pre-release code review of the work since 2026-11-08 (177 commits) | 2026-11-11 | — | branch `fase-5-piano` | passed after fixes | No Must. Three defects fixed with a test that failed first: D-F9-01 (restore all or none), D-F9-02 (calorie estimate from the time-weighted HR: a third too low with smart recording), D-F9-03 (page title date with an offset). Then `uv run --locked nox`: 5,765 tests passed, 17 skipped. |

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
