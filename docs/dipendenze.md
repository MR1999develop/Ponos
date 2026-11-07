# Dependency register

Register of the direct dependencies of `pyproject.toml`: runtime, the `dev`
group and the build backend. Validation: **2026-11-05**.

## Register rules

- **Allowed licences** (compatible with GPLv3): MIT, BSD, Apache-2.0, ISC,
  PSF, LGPL, GPL-3.0. Any other licence is a problem to report and is not
  approved without an explicit decision, recorded in this file.
- **Waiting period for new versions**: `exclude-newer = "7 days"` in
  `[tool.uv]` of `pyproject.toml`. A version published less than 7 days ago
  does not enter the lock. Exception: security fixes, decided by hand and
  noted here, with the diff read.
- **Vulnerabilities**: `uv run --locked nox -s audit` exports the runtime
  dependencies from the lock and checks them with `pip-audit`
  (`--require-hashes`). The session is part of `uv run --locked nox`, so of
  the CI.
- **Every direct dependency** has an entry with: version pinned in
  `uv.lock`, licence read from the installed package metadata, purpose in the
  project, date of the last release, signs of maintenance, alternatives
  considered and date of validation.
- **Update**: a new version changes the entry and records a new validation
  date.

## Validation result (2026-11-05)

- **Audit** (`uv run --locked nox -s audit`): `No known vulnerabilities
  found`. Result also checked on the whole environment, dev included, with
  `uv run --locked pip-audit`: no vulnerability. The `ponos-coach` package is
  skipped because it is not on PyPI: this is expected.
- **Licences to report** (not approved):
  - `hypothesis` 6.170.13: **MPL-2.0**, not in the list. It is only in the
    `dev` group and does not enter the distributed package.
  - `numpy` 2.5.4: the licence expression is `BSD-3-Clause AND 0BSD AND MIT
    AND Zlib AND CC0-1.0`. `0BSD`, `Zlib` and `CC0-1.0` are not in the list.
    Numpy is a runtime dependency and ends up in the package: we must check
    which components are really distributed in the wheel.
- **Licence notes** (allowed, but with obligations to meet): `pyside6` (see
  entry), `plotly` with `plotly.js` included (MIT).
- **Recent releases** (less than 7 days old at the validation date): no
  pinned version in the lock. Not pinned and newer than the lock:
  `platformdirs` 4.14.3, 4.14.4, 4.14.5 (1-4 November 2026) and `uv_build`
  0.13.7 (3 November 2026). `hypothesis` 6.170.14 (30 October 2026) is 6 days
  old. None is a declared security fix.
- **Maintenance to watch**: `fitdecode` has no release or push since 6 August
  2025 (fifteen months). `pytest-qt` has its last release on 1 July 2025, but
  the repository is active. `desktop-notifier` has its last release on 8
  August 2025, with a push on 16 September 2026.

## Entries

### pyside6 (runtime)

- Pinned version: **6.12.0** (released 2026-10-08).
- Licence: `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only` (installed
  metadata). The project uses the **LGPL-3.0** option, compatible with GPLv3.
- LGPL obligation: the app is used without changes to the Qt libraries. In
  the distributed package the Qt libraries must remain replaceable. To check
  in phase 8 with PyInstaller. QtWebEngine (pyside6-webengine, pyside6-addons,
  pyside6-essentials, pyside6-pdf, shiboken6) brings Chromium, with third-party
  licences: these too must be checked in phase 8.
- Purpose: Qt interface (window, model/view tables, dialogs) and
  `QWebEngineView` for the Plotly charts (ADR 001).
- Last release: 6.12.0 (2026-10-08); repository `pyside/pyside-setup` with a
  push on 2026-11-05, not archived.
- Alternatives: pyqtgraph with PySide6 (rejected, ADR 001: less interactive
  charts); Rust + egui and Tauri + ECharts (rejected, ADR 001). PyQt6 was not
  evaluated in the ADR.
- Validated: 2026-11-05.

### plotly (runtime)

- Pinned version: **7.1.0** (released 2026-09-15).
- Licence: **MIT** (installed metadata). The `plotly.js` included in the
  package is **v4.1.1, MIT** (header of `plotly.min.js`), and will be
  distributed in the app.
- Purpose: interactive load charts (PMC, zones, HR) in the `QWebEngineView`
  (ADR 001). The chart JSON comes from the domain; the page has no network
  access.
- Last release: 7.1.0 (2026-09-15); repository `plotly/plotly.py` with a push
  on 2026-11-03, not archived, many open issues (752).
- Alternatives: pyqtgraph (rejected, ADR 001); ECharts in Tauri (rejected, ADR
  001).
- Validated: 2026-11-05.

### garminconnect (runtime)

- Pinned version: **0.3.20** (released 2026-10-23).
- Licence: **MIT** (installed metadata).
- Warning: it uses an **unofficial login** to Garmin Connect. Garmin offers no
  API to private users. The library may stop working at every login change,
  and the use may be against Garmin's terms of use. See
  `docs/adr/002-accesso-garmin.md`: the adapter is isolated behind the
  `ActivitySource` port, with manual FIT import always available.
- Purpose: download activities, FIT files and calendar from the direct login
  to Garmin Connect, with MFA.
- Last release: 0.3.20 (2026-10-23); repository
  `cyberjunky/python-garminconnect` with a push on 2026-10-31, not archived.
- Alternatives: `garth` (broken by Garmin in March 2026, as ADR 002 reports);
  intervals.icu as an intermediary (option 2 of ADR 002, to add with a new ADR
  if needed).
- Validated: 2026-11-05.

### fitdecode (runtime)

- Pinned version: **0.11.0** (released 2025-08-06).
- Licence: **MIT** (installed metadata).
- Purpose: reading FIT files (activities imported by hand or from a folder).
  The FIT reader is a trust boundary: it is fuzzed with `hypothesis`
  (T-SEC-01).
- Last release: 0.11.0 (2025-08-06); repository `polyvertex/fitdecode` with
  last push on 2025-08-06. **Sign of poor maintenance**: no release or push
  for fifteen months. To reconsider at every phase that touches the FIT
  reader.
- Alternatives: Garmin's official FIT SDK (`garmin-fit-sdk`) to evaluate; no
  alternative has been discussed in the project.
- Validated: 2026-11-05.

### numpy (runtime)

- Pinned version: **2.5.4** (released 2026-10-10).
- Licence: `BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0` (installed
  metadata). **To report**: `0BSD`, `Zlib` and `CC0-1.0` are not in the list
  of allowed licences. See the notes above.
- Planned purpose: numerical calculations on contiguous collections of
  values, for example the exponential averages of ATL, CTL and TSB
  (PLAN.md). To be confirmed in the domain phases.
- Last release: 2.5.4 (2026-10-10); repository `numpy/numpy` with a push on
  2026-11-04, not archived.
- Alternatives: the standard library (`statistics`, `math`) for simple
  calculations; no alternative has been discussed in the project.
- Validated: 2026-11-05, with the licence to report above.

### keyring (runtime)

- Pinned version: **25.7.0** (released 2025-11-16).
- Licence: **MIT** (installed metadata).
- Purpose: Garmin tokens in the Secret Service on Ubuntu, tied to the profile
  id, never in the data folder (CLAUDE.md, ADR 003, ADR 006). On Windows the
  tokens do not go through `keyring`: they are encrypted with DPAPI (ADR 006).
- Last release: 25.7.0 (2025-11-16); repository `jaraco/keyring` with a push
  on 2026-10-29, not archived.
- Alternatives: direct calls to the system APIs (`ctypes` on Windows,
  `secretstorage` on Linux); not discussed in the project.
- Validated: 2026-11-05.

### desktop-notifier (runtime)

- Pinned version: **6.2.0** (released 2025-08-08).
- Licence: **MIT** (installed metadata).
- Purpose: end-of-synchronisation notification (toast on Windows, desktop
  notification on Ubuntu; PLAN.md).
- Last release: 6.2.0 (2025-08-08); repository `samschott/desktop-notifier`
  with a push on 2026-09-16, not archived. The release is fifteen months old,
  but the repository is active.
- Alternatives: `plyer`; native notifications for each platform. Not
  discussed in the project.
- Validated: 2026-11-05.

### platformdirs (runtime)

- Pinned version: **4.14.0** (released 2026-10-28).
- Licence: **MIT** (installed metadata).
- Purpose: standard system folders for the app configuration and the list of
  profiles (PLAN.md, section 4.2).
- Last release: 4.14.5 (2026-11-04), newer than the lock; the lock keeps
  4.14.0, and the releases 4.14.3-4.14.5 are less than 7 days old. Repository
  `tox-dev/platformdirs` with a push on 2026-11-04, not archived.
- Alternatives: `appdirs` (no longer developed, replaced by platformdirs);
  hand-written paths for each system.
- Validated: 2026-11-05.

### pytest (dev group)

- Pinned version: **9.2.0** (released 2026-10-27).
- Licence: **MIT** (installed metadata).
- Purpose: unit, use-case and integration tests.
- Last release: 9.2.0 (2026-10-27); repository `pytest-dev/pytest` with a push
  on 2026-11-05, not archived.
- Alternatives: `unittest` (standard library), more verbose.
- Validated: 2026-11-05.

### pytest-qt (dev group)

- Pinned version: **4.5.0** (released 2025-07-01).
- Licence: **MIT** (installed metadata).
- Purpose: tests of the Qt GUI in `offscreen` mode (sections 4 and 5 of the
  plan).
- Last release: 4.5.0 (2025-07-01); repository `pytest-dev/pytest-qt` with a
  push on 2026-11-02, not archived. The release is sixteen months old.
- Alternatives: none in the Qt field; manual tests or writing a fixture of
  our own on the `QApplication`.
- Validated: 2026-11-05.

### hypothesis (dev group)

- Pinned version: **6.170.13** (released 2026-10-28).
- Licence: **MPL-2.0** (installed metadata). **To report**: MPL-2.0 is not in
  the list of allowed licences. It is only in the `dev` group and does not
  enter the distributed package. Not approved: an explicit decision is needed.
- Purpose: property tests and fuzzing of the FIT reader and the parsers
  (T-SEC-01).
- Last release: 6.170.13 (2026-10-28); 6.170.14 (2026-10-30) is newer and is
  not in the lock. Repository `HypothesisWorks/hypothesis` with a push on
  2026-11-03, not archived.
- Alternatives: `pytest` with hand-written inputs; `atheris` for pure
  fuzzing. Not discussed in the project.
- Validated: 2026-11-05, with the licence to report above.

### ruff (dev group)

- Pinned version: **0.17.3** (released 2026-10-27).
- Licence: **MIT** (installed metadata).
- Purpose: formatting and lint (`ruff format --check`, `ruff check`) in
  `uv run --locked nox`.
- Last release: 0.17.3 (2026-10-27); repository `astral-sh/ruff` with a push
  on 2026-11-05, not archived.
- Alternatives: `black` and `flake8` with `isort`, more separate tools.
- Validated: 2026-11-05.

### mypy (dev group)

- Pinned version: **2.4.1** (released 2026-10-28).
- Licence: **MIT** (installed metadata).
- Purpose: type checking in `--strict` mode on `src`, `tests`, `experiments`
  and `noxfile.py`.
- Last release: 2.4.1 (2026-10-28); repository `python/mypy` with a push on
  2026-11-05, not archived.
- Alternatives: `pyright` (alternative type checker).
- Validated: 2026-11-05.

### import-linter (dev group)

- Pinned version: **2.15** (released 2026-09-04).
- Licence: **BSD 2-Clause** (installed metadata, BSD classifier).
- Purpose: architecture contracts (`lint-imports`): the domain is pure, the UI
  does not talk to the adapters, the adapters are independent.
- Last release: 2.15 (2026-09-04); repository `seddonym/import-linter` with a
  push on 2026-11-05, not archived.
- Alternatives: hand-written tests that analyse the `import`s (for example
  with `grimp`); not discussed in the project.
- Validated: 2026-11-05.

### pip-audit (dev group)

- Pinned version: **2.10.1** (released 2026-06-10).
- Licence: **Apache-2.0** (`LICENSE` text in the installed metadata).
- Purpose: vulnerability check of the pinned dependencies, in the `audit`
  session.
- Last release: 2.10.1 (2026-06-10); repository `pypa/pip-audit` with a push
  on 2026-10-30, not archived.
- Alternatives: `safety` (paid database for some uses); Google's
  `osv-scanner`.
- Validated: 2026-11-05.

### nox (dev group)

- Pinned version: **2026.10.20** (released 2026-10-20).
- Licence: **Apache-2.0** (installed metadata).
- Purpose: check sessions (`uv run --locked nox`) that the CI runs identical
  to the local ones, and the `dist` and `collaudo` sessions.
- Last release: 2026.10.20 (2026-10-20); repository `wntrblm/nox` with a push
  on 2026-11-05, not archived.
- Alternatives: `tox`; a `Makefile` with scripts. Not discussed in the
  project.
- Validated: 2026-11-05.

### uv_build (build backend)

- Version: **not pinned in `uv.lock`**. The backend is in `[build-system]`
  (`uv_build>=0.9`) and is resolved by the isolated build; it is not in the
  lock. To evaluate whether to pin a version.
- Last release: 0.13.7 (2026-11-03), **less than 7 days old** at the
  validation date: it is not in the lock and is not a security fix.
- Licence: **MIT OR Apache-2.0** (expression from the PyPI metadata; the
  package is not installed in the environment, so the licence is not read from
  installed metadata).
- Purpose: building the project package (`uv build`).
- Repository `astral-sh/uv` with a push on 2026-11-05, not archived.
- Alternatives: `hatchling`, `setuptools`.
- Validated: 2026-11-05.
