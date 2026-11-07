# 001. Stack: Python, PySide6 and Plotly

- Status: Accepted
- Date: 2026-11-05

## Context

Ponos Coach is a desktop app for Windows and Ubuntu with activity tables,
interactive load charts (PMC, zones, HR) and an adapter to Garmin Connect.
Changing the stack after the start is costly. Charts and tables are the heart
of the interface; access to Garmin depends on an existing library.

## Options

1. **Python + PySide6 + pyqtgraph.**
   - Pro: light package, fast native charts.
   - Con: less polished and less interactive charts (legends, tooltips, vector
     export, themes) to build by hand.
2. **Python + PySide6 + Plotly in `QWebEngineView`.**
   - Pro: better and interactive charts, SVG and PNG export, same language as
     the Garmin library.
   - Con: heavier package (about 250 MB); a web engine inside the app, to be
     isolated (no network, no bridge to the code); chart data to be passed as
     JSON.
3. **Rust + egui.**
   - Pro: it is the usual stack of desktop projects, a single light
     executable.
   - Con: the Garmin login must be rewritten from scratch; plainer charts and
     tables; more work for the same functions.
4. **Tauri (Rust) + TypeScript + ECharts.**
   - Pro: rich charts, lighter package than Qt WebEngine.
   - Con: two languages and two toolchains; no skill for the frontend; the
     Garmin login must be rewritten anyway.

## Decision

Option 2: Python 3.13, PySide6 (Qt 6), Plotly charts in a `QWebEngineView`
with `plotly.js` included. Choice of the user: better charts against a heavier
package. The rest of the stack is in `PLAN.md` section 4.1.

## Consequences

- The package weighs about 250 MB: the value must be measured in phase 8.
- The charts page is a trust boundary: only local content, network requests
  blocked, data as encoded JSON, no `QWebChannel` unless a need is declared
  (see `docs/threat-model.md`).
- The domain stays pure and independent of Qt: if the stack has to change,
  interface and adapters are rewritten, not the domain.
- Direct dependencies are validated and recorded in `docs/dipendenze.md`.

## How compliance is verified

- `lint-imports` in `uv run --locked nox`: the domain does not import Qt,
  network or I/O; dependencies go only `ui → use cases → domain`.
- Test T-SEC-06 on the charts page; T-UI-04 on the charts.
- Phase 8: package size measured and recorded.
- A change of stack requires a new ADR that replaces this one.
