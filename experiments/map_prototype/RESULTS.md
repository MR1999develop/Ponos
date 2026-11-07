# Map prototype: results (2026-11-07)

Throw-away prototype for ADR 010. It measures two libraries in a `QWebEngineView` with
50,000 synthetic activities. Nothing here is product code.

## Environment

- Windows 10 Pro 10.0.19045, 12 logical CPUs, NVIDIA GeForce RTX 3050 (WebGL through
  ANGLE/Direct3D 11), window 1280 x 800.
- Python 3.13.16, PySide6 6.12.0 (QtWebEngine), page served from the app's `ponos://app/`
  scheme with a CSP of `default-src 'none'`; no network request at all (background is a
  flat grey, no tiles).
- Libraries from npm, fixed versions, SHA-512 of the tarball compared with the registry's
  `dist.integrity` by `fetch_vendor.py`:
  - `maplibre-gl@5.24.0` (BSD-3-Clause), CSP build + worker file:
    `sha512-ALyFxgtd5R+65UqZ/++lOqwWcC0SNho9c27fYSyLmG7AfnAul2o46F05aDJGPbFU57wos9dgcIySHs0Xe6ia3A==`
  - `leaflet@1.9.4` (BSD-2-Clause):
    `sha512-nxS1ynzJOmOlHp+iL3FyWqK89GtNL8U8rvlMOsQdTTssxZwCXh8N2NB3GDQOL+YR3XnWyZAxwQixURb+FA74PA==`
  - `leaflet.markercluster@1.5.3` (MIT):
    `sha512-vPTw/Bndq7eQHjLBVlWpnGeLa3t+3zGiuM7fJwCkiMFq+nmRuG3RI3f7f4N4TDX7T4NpbAXpR2+NTRSEGfCSeA==`
  - MapLibre 6.16.0 is ESM-only (`.mjs`): it cannot be loaded with a plain script tag, so the
    5.24.0 UMD build was measured. Whether our scheme can serve modules was not tested.

## Data and method

- `generate_data.py 50000`: 25 invented cities (half of the activities in a few), 50,000
  starts, 2,499,579 track points (20 to 80 per track, 100 to 400 m steps, persistent heading).
  No real data.
- Three scenarios per library, each in a fresh process:
  - `starts`: clustered start points only (MapLibre: GeoJSON `cluster: true`, supercluster in a
    worker, circle layers; Leaflet: `circleMarker` on a canvas renderer in a
    `markerClusterGroup`, `chunkedLoading`).
  - `visible` (the proposed design): starts plus the tracks of the activities in view, drawn
    only at zoom 11 or more and with at most 300 activities.
  - `tracks`: starts plus all 50,000 tracks drawn at once (stress test).
- First draw: from creating the map to idle (MapLibre `idle`; Leaflet clusters built plus two
  animation frames).
- 40 deterministic operations: 20 jumps (to the start of a random activity, zoom 3, 5, 7, 9, 11
  or 13, no animation) and 20 pans (about 18% of the view, at the current zoom). An operation
  ends when the map reports idle (MapLibre) or after two animation frames (Leaflet). The time of
  an operation includes the scan of the 50,000 starts that builds the list of visible activities
  (0.5 to 0.8 ms at p95, measured on its own in the `list` column). Each operation has a 20 s
  timeout; none was reached.
- Memory: working set (current and peak) of the main process and all its children (renderer,
  GPU), read after the run; plus `usedJSHeapSize`.
- One run per scenario. Resolution: two display frames, about 33 ms, so values around 33 ms are
  "at the floor", not equal speed.

## Numbers (ms unless stated)

| Library | Scenario | First draw | Jump p50 / p95 / max | Pan p50 / p95 / max | List scan p95 | Timeouts | JS heap MB | Memory MB (now / peak) |
|---|---|---|---|---|---|---|---|---|
| MapLibre | starts | 440 | 33 / 35 / 44 | 32 / 34 / 35 | 0.5 | 0 | 148 | 676 / 718 |
| MapLibre | visible | 449 | 34 / 39 / 44 | 33 / 34 / 35 | 0.5 | 0 | 123 | 907 / 947 |
| MapLibre | tracks | 4369 | 99 / 484 / 500 | 33 / 51 / 52 | 0.8 | 0 | 199 | 3394 / 3436 |
| Leaflet | starts | 326 | 34 / 52 / 52 | 33 / 34 / 50 | 0.7 | 0 | 177 | 577 / 609 |
| Leaflet | visible | 334 | 34 / 52 / 184 | 34 / 35 / 55 | 0.5 | 0 | 188 | 751 / 754 |
| Leaflet | tracks | 1265 | 1090 / 1864 / 1880 | 276 / 952 / 1224 | 0.6 | 0 | 747 | 2260 / 2691 |

Without a GPU (`QTWEBENGINE_CHROMIUM_FLAGS=--disable-gpu`):

- MapLibre: the WebGL context cannot be created ("Failed to create GLES3 context"); the map
  does not start (the run reports an error and no result). Software WebGL through SwiftShader
  was tried with the usual flags and did not take effect in this setup, so it was not measured.
- Leaflet (canvas 2D, same 40 operations): `starts` first draw 369, jump 34 / 72 / 72, pan
  33 / 50 / 53, memory 815 MB; `visible` first draw 385, jump 34 / 72 / 261, pan 34 / 36 / 37,
  memory 735 MB.

## Reading the numbers

- The budget of T-MAP-05 (0.4 s at p95, list included) is met by both libraries in the design
  scenario (`visible`) and in `starts`, by about a factor of ten.
- Drawing every track is not viable: MapLibre needs 4.4 s for the first draw and 3.4 GB; Leaflet
  is faster to start but jumps take about 1 s and pans up to 1 s (the canvas repaints all 2.5
  million points). The design therefore sends the page only the starts and the tracks in view.
- Leaflet is lighter (about 100 to 150 MB less in the realistic scenarios) and works without a
  GPU; MapLibre does not start without WebGL.
- Not measured: cluster count labels in MapLibre (they need glyph files served locally; the
  prototype draws circles only, Leaflet draws real numbered DOM icons), real tile loading
  (no network in the prototype), animated cluster split and merge, a second machine, an older
  GPU, the effect of a high-DPI display.

## Recommendation

Leaflet 1.9.4 with Leaflet.markercluster 1.5.3. The budget is met with a wide margin, it needs
no GPU, it is lighter, and numbered circles with animated split and merge and click-to-zoom are
built in, which is the behaviour the PLAN asks for. Its weaknesses (markercluster unmaintained
since 2022, slow with thousands of tracks) are covered by vendoring with a pinned hash and by the
"tracks in view only" design. MapLibre GL JS is the way out if an offline vector map is wanted
later.

## Reproducing

```
uv run python experiments/map_prototype/fetch_vendor.py      # npm pack, hash check
uv run python experiments/map_prototype/generate_data.py 50000
uv run python experiments/map_prototype/run_benchmark.py all 40
```

`vendor/`, `data.js` and `results-*.json` are ignored by git (large or generated); the JSON of
this run was `results-20261107.json`.
