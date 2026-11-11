# Dependency register

Register of the direct dependencies of `pyproject.toml`: runtime, the `dev`
group and the build backend. Last validation: **2026-11-09**.

## Register rules

- **Allowed licences** (compatible with GPLv3): MIT, BSD (2- and 3-clause),
  0BSD, Zlib, CC0-1.0, Apache-2.0, ISC, PSF, MPL-2.0, LGPL, GPL-3.0. The list
  was aligned on 2026-11-09 with the project's licence policy; the decisions
  taken on 2026-11-09 are recorded in the validation section of that date.
  Any other licence is a problem to report and is not approved without an
  explicit decision, recorded in this file.
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
- **Licences to report** (both resolved on 2026-11-09, see that section):
  - `hypothesis` 6.170.13: **MPL-2.0**, not in the list. It is only in the
    `dev` group and does not enter the distributed package.
  - `numpy` 2.5.4: the licence expression is `BSD-3-Clause AND 0BSD AND MIT
    AND Zlib AND CC0-1.0`. `0BSD`, `Zlib` and `CC0-1.0` are not in the list.
    Numpy is a runtime dependency and ends up in the package: we must check
    which components are really distributed in the wheel.
- **Licence notes** (allowed, but with obligations to meet): `pyside6` (see
  entry), `plotly` with `plotly.js` included (MIT), `numpy` (GCC runtime under
  GPL-3.0-or-later with the GCC Runtime Library Exception; the MSVC runtime
  bundled in the wheel is still to decide, see the entry).
- **Recent releases** (less than 7 days old at the validation date): no
  pinned version in the lock. Not pinned and newer than the lock:
  `platformdirs` 4.14.3, 4.14.4, 4.14.5 (1-4 November 2026) and `uv_build`
  0.13.7 (3 November 2026). `hypothesis` 6.170.14 (30 October 2026) is 6 days
  old. None is a declared security fix.
- **Maintenance to watch**: `fitdecode` has no release or push since 6 August
  2025 (fifteen months). `pytest-qt` has its last release on 1 July 2025, but
  the repository is active. `desktop-notifier` has its last release on 8
  August 2025, with a push on 16 September 2026.

## Validation result (2026-11-08)

- **Audit** (`uv run --locked pip-audit`, same check as `nox -s audit`):
  `No known vulnerabilities found`. The `ponos-coach` package is skipped
  because it is not on PyPI: this is expected.
- **Licence reviewed and approved**: `pyinstaller` 6.22.3,
  `GPL-2.0-or-later WITH Bootloader-exception`. "Or later" lets it be used
  under GPL-3.0, the licence of this project; it is a build tool, and the
  only part shipped is the bootloader, which the exception covers. See the
  entry below.
- **New direct dependency**: `pyinstaller` (dev group), added to
  `pyproject.toml` before this validation. Released 2026-09-12, so it is not a
  new release under the waiting period.

## Validation result (2026-11-09)

- **Audit** (`uv run --locked pip-audit`, run on the locked environment):
  `No known vulnerabilities found`. The `ponos-coach` package is skipped
  because it is not on PyPI: this is expected.
- **New direct dependency**: `tzdata` 2026.6 (runtime), missing from the
  register; see its entry. Uploaded on 2026-10-28, twelve days old at the
  validation date: outside the waiting period. No version in the lock changed
  in this validation.
- **Licence decisions**. Each component was read from the installed wheel in
  `.venv` (`numpy-2.5.4.dist-info/licenses/`, `numpy.libs/`) or from the built
  package (`dist/app/PonosCoach/_internal`).
  - `numpy` 2.5.4, components of the wheel:
    - `0BSD`: pythoncapi-compat (`COPYING`, BSD Zero Clause). **Approved.**
    - `Zlib`: libdivide (`numpy/_core/include/numpy/libdivide/LICENSE.txt` and
      `numpy/_core/include/numpy/random/LICENSE.txt`). **Approved.**
    - `CC0-1.0`: only the file `hwy/contrib/random/random-inl.h` of Highway
      (`numpy/_core/src/highway/LICENSE`; Highway itself is Apache-2.0 or
      BSD-3-Clause). **Approved.**
    - `BSD-3-Clause` (NumPy, OpenBLAS, pocketfft, x86-simd-sort, and so on),
      `BSD-3-Clause-Open-MPI` (LAPACK, in OpenBLAS): **approved**.
    - `MIT` (PCG64, SFC64 and others, as in the NumPy metadata): **approved**.
    - `NCSA` (`numpy/random/LICENSE.md`): dual licence with BSD-3-Clause; the
      BSD-3-Clause option is taken. The NCSA text stays in the package.
      **Decided: BSD-3-Clause option.**
    - `GPL-3.0-or-later WITH GCC-exception-3.1`: GCC runtime library (libgfortran)
      statically linked into `numpy.libs/libscipy_openblas64_*.dll`. It is in the
      GPL-3.0 family, and the GCC Runtime Library Exception allows the
      combination with a program under GPL-3.0. The text is in the wheel's
      `LICENSE.txt`, which is in the built package. **Approved.**
    - Public domain dedication (`numpy/random/src/splitmix64/LICENSE.md`, Vigna):
      a waiver of rights, not a licence with terms. **To report**: it is not in
      the list; decision needed.
    - `msvcp140.dll` (`numpy.libs/`, Microsoft C++ runtime bundled by delvewheel):
      not named in NumPy's `LICENSE.txt`. Its licence is the Microsoft runtime
      redistribution terms, not an open-source licence. **To report: not approved,
      decision needed.** To check whether the PyInstaller package carries the same
      runtime.
  - `hypothesis` 6.170.13 (MPL-2.0, dev group): **approved for this dev-only
    tool.** It is not in the built package (no `hypothesis` distribution in
    `dist/app/PonosCoach/_internal`). MPL-2.0 is file-level and allows the files
    to be combined in a larger work under GPL (section 3.3).
  - `certifi` 2026.10.23 (MPL-2.0, runtime, transitive): **approved.** The files
    in the package (`certifi/cacert.pem`, `certifi/py.typed`) are identical by
    SHA-256 to the installed ones. See the entry. **To close before release**: the
    package has no MPL-2.0 text and no source reference for certifi.
  - `pyinstaller` 6.22.3: the approval of 2026-11-08 is confirmed. The line "not
    approved, decision needed" of 2026-11-05 is superseded.
  - Other distributions in the built package not in the register (transitive):
    `curl_cffi` 0.17.0 (MIT), `pluggy` 1.7.0 (MIT), `importlib_metadata` 8.7.1
    (Apache-2.0). All in the list.
- **Open before release** (not decided here): the LGPL obligations of
  PySide6/Qt and QtWebEngine, see the `pyside6` entry.
- **Maintenance**: `tzdata` repository (`python/tzdata`) with a push on
  2026-11-01, not archived; `certifi` repository (`certifi/python-certifi`) with
  a push on 2026-11-04, not archived.

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
- **To close before release** (checked on 2026-11-09 in
  `dist/app/PonosCoach/_internal`; not decided here): the package has no
  LGPL-3.0 text, no link to the sources and no third-party notice for
  QtWebEngine. What is needed:
  1. the LGPL-3.0 text in the package. The wheel's `licenses/` folder holds
     `LicenseRef-Qt-Commercial.txt`, which is the commercial licence, not the
     LGPL text;
  2. a link to the Qt and PySide sources (Qt download site, and
     `pyside/pyside-setup`) and a statement that the Qt libraries are shipped as
     separate dynamic files (`Qt6*.dll`, `PySide6/*.pyd`), so they can be
     replaced; the check that the replacement works is to be done on the built
     package;
  3. for QtWebEngine (Chromium and its third-party components): the third-party
     credits and licences that Qt ships with QtWebEngine, in the package.
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
  metadata). **Decided 2026-11-09**: the components were read in the installed
  wheel; `0BSD`, `Zlib` and `CC0-1.0` are approved, the GCC runtime
  (`GPL-3.0-or-later WITH GCC-exception-3.1`) is approved, the MSVC runtime
  `msvcp140.dll` is **not approved, decision needed**. See the validation of
  that date.
- Planned purpose: numerical calculations on contiguous collections of
  values, for example the exponential averages of ATL, CTL and TSB
  (PLAN.md). To be confirmed in the domain phases.
- Last release: 2.5.4 (2026-10-10); repository `numpy/numpy` with a push on
  2026-11-04, not archived.
- Alternatives: the standard library (`statistics`, `math`) for simple
  calculations; no alternative has been discussed in the project.
- Validated: 2026-11-09 (licence decided on that date).

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
- Licence: **MPL-2.0** (installed metadata). **Decided 2026-11-09**: approved
  for this dev-only tool; it does not enter the distributed package (see the
  validation of that date).
- Purpose: property tests and fuzzing of the FIT reader and the parsers
  (T-SEC-01).
- Last release: 6.170.13 (2026-10-28); 6.170.14 (2026-10-30) is newer and is
  not in the lock. Repository `HypothesisWorks/hypothesis` with a push on
  2026-11-03, not archived.
- Alternatives: `pytest` with hand-written inputs; `atheris` for pure
  fuzzing. Not discussed in the project.
- Validated: 2026-11-09 (licence decided on that date).

### tzdata (runtime)

- Pinned version: **2026.6** (uploaded 2026-10-28).
- Licence: **Apache-2.0** (installed metadata; the wheel's `LICENSE`, "Apache
  Software License 2.0", copyright Paul Ganssle and Stan Ulbrych). The wheel does
  not state the licence of the IANA time zone data it ships; upstream the IANA
  tz database is described as public domain. **To check** on the built package
  before release.
- Purpose: IANA time zone data for `zoneinfo.ZoneInfo`, used by the profiles
  (`profiles/timezones.py`) and by the activity, load, assessment and nutrition
  code. Windows has no system zone database, so the app needs this package
  (declared in `pyproject.toml`).
- Last release: 2026.6 (2026-10-28), twelve days old at validation; repository
  `python/tzdata` with a push on 2026-11-01, not archived.
- Alternatives: the system zone database (not on Windows); `pytz` (MIT, its
  own copy of the data, not used with `zoneinfo`); `python-dateutil` (its own
  zone data, not used).
- Registered and validated: 2026-11-09 (the entry was missing; found by the
  conformity check).

### certifi (runtime, transitive)

- Pinned version: **2026.10.23** (uploaded 2026-10-23).
- Licence: **MPL-2.0** (installed metadata; the wheel's `LICENSE`). Weak,
  file-level copyleft: it covers the certifi files only.
- Not a direct dependency: required by `requests` and `curl-cffi` in the lock.
  It is in the built package (`certifi/cacert.pem`, `certifi/py.typed`), and
  both files are identical by SHA-256 to the installed ones.
- Purpose: the CA bundle (Mozilla root store) for the verification of TLS
  certificates of the HTTPS calls made by those libraries.
- Decision **2026-11-09**: approved. The MPL-2.0 files are distributed
  unchanged, and MPL-2.0 section 3.3 allows them in a larger GPL work.
- **To close before release**: the built package has no certifi `LICENSE` and no
  source reference. Add the MPL-2.0 text and the link to
  `https://github.com/certifi/python-certifi` to the notices of the package.
- Last release: 2026.10.23 (2026-10-23); repository `certifi/python-certifi`
  with a push on 2026-11-04, not archived.
- Alternatives: the certificate store of the system (not discussed in the
  project).
- Validated: 2026-11-09.

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

### pyinstaller (dev group)

- Pinned version: **6.22.3** (released 2026-09-12).
- Licence: `GPLv2-or-later with a special exception` (installed metadata, free
  text; `licenses/COPYING.txt` gives the SPDX form `GPL-2.0-or-later WITH
  Bootloader-exception`). **Approved on 2026-11-08** (see the validation of
  that date).
- Reasoning to evaluate: the exception allows to embed the compiled bootloader
  and related files in programs distributed under any licence. PyInstaller is
  used only to build the package (`nox -s dist`); the bootloader is the part
  that ends up in the distributed executable. The PyInstaller Python code is
  not part of the application. This must be verified on the built artefact in
  phase 8 (the bootloader files and the licence files in the package).
- Purpose: build of the PyInstaller executable (`uv run --locked nox -s dist`),
  with smoke test and `SHA256SUMS`. Dev group only: not a runtime dependency.
- Last release: 6.22.3 (2026-09-12); repository `pyinstaller/pyinstaller` with
  a push on 2026-11-08, not archived, 118 open issues.
- Alternatives considered (not adopted): **Nuitka** (licence on PyPI:
  AGPL-3.0, to report; compiles to C, slower builds); **cx_Freeze** (no
  licence in the PyPI metadata, not evaluated); **briefcase** (no licence in
  the PyPI metadata, not evaluated; packages for store and installers, more
  than needed here).
- Validated: 2026-11-08 (licence approved on that date).

### ANSES-CIQUAL and CREA food tables (removed)

- Removed on 2026-11-10: the meals show only energy and macronutrients (the
  user's decision), so the app no longer ships a food table. The ANSES-CIQUAL
  2025 values (Etalab-2.0 licence, registered 2026-11-07, replaced USDA
  FoodData Central on 2026-11-09) and the CREA comparison (never copied) are no
  longer used; `foods.json`, `experiments/verify_foods.py` and
  `experiments/crea_mapping.json` are gone, and the README no longer carries the
  ANSES attribution. See ADR 008, amendment of 2026-11-10.

### Leaflet (JavaScript library, vendored, runtime of the map page)

- Version: **1.9.4**, from the npm registry (package `leaflet`). Not a Python
  dependency: the files are in `src/ponos_coach/ui/web/vendor/`
  (`leaflet.js`, `leaflet.css`), served by the `ponos://` scheme; no CDN.
- Hash: SHA-512 of the registry tarball, verified by
  `tools/vendor_map_libraries.py` against the registry's own `dist.integrity`:
  `sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`.
  The SHA-256 of every packaged file is in `vendor/VENDOR.json` and a test
  (`tests/ui/test_vendor_libraries.py`) fails if a file or a version changes.
- Licence: **BSD-2-Clause** (read from the tarball `package.json`), text in
  `vendor/LICENSE-leaflet.txt`. Attribution: "Leaflet, (c) Volodymyr
  Agafonkin and CloudMade"; the licence file ships with the package.
- Purpose: the map view (ADR 010). Leaflet 2 is still alpha: 1.9.x stays.
- Alternatives: MapLibre GL JS (measured, rejected: needs WebGL), see ADR 010.
- Validated: 2026-11-08.

### Leaflet.markercluster (JavaScript library, vendored, runtime of the map page)

- Version: **1.5.3**, npm package `leaflet.markercluster`, files
  `leaflet.markercluster.js`, `MarkerCluster.css`, `MarkerCluster.Default.css`
  in `src/ponos_coach/ui/web/vendor/`.
- Hash: SHA-512 of the tarball (`dist.integrity`):
  `sha512-vPTw/Bndq7eQHjLBVlWpnGeLa3t+3zGiuM7fJwCkiMFq+nmRuG3RI3f7f4N4TDX7T4NpbAXpR2+NTRSEGfCSeA==`,
  verified like Leaflet's.
- Licence: **MIT**, text in `vendor/LICENSE-leaflet.markercluster.txt`.
- Purpose: numbered groups of start points that split and merge with the zoom.
- Maintenance: no release since 2022; stable, pinned by hash, nobody to fix
  bugs for us (accepted in ADR 010).
- Validated: 2026-11-08.

### qwebchannel.js (from Qt, not vendored by us)

- Read at run time from PySide6's own resources (`:/qtwebchannel/`) and served
  to the map page; it comes with Qt (LGPL-3.0 / GPL), covered by the `pyside6`
  entry. It is the only script of the map page not under `web/`.

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
