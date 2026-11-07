# 010. Map view: library, tile source, cache and page isolation

- Status: Proposed (draft, to be accepted when phase 9 starts)
- Date: 2026-11-07

## Context

Phase 9 adds the Map view: activities shown as start points grouped in numbered
circles that split and merge with the zoom, a click on a group zooms into its
area, a click on an activity selects it and draws its track, and a list of the
visible activities sits on the right (PLAN, sections 4.x and 4.9). Five choices
are hard to change once released: the map library, the tile source, what the
page receives from Python, where tiles are cached, and which network access
the map page gets.

Constraints:

- the page is isolated like the charts page: `ponos://app/` scheme, CSP with
  `default-src 'none'`, no `file://`, no CDN, libraries from npm with a fixed
  version, SHA-512 checked against the registry, licence recorded;
- licences must be compatible with GPLv3 (BSD-2, BSD-3 and MIT are);
- GPS positions are personal data: never in logs, never in files outside the
  profile folder; the tile host unavoidably sees the areas that are looked at
  (risk to be declared in the threat model, absent with an offline map);
- the tile key is a secret: operating-system key store, never in the profile
  folder, in files, in logs or in the page source;
- the map is a convenience: the app must work, and say why, without a key or
  without network (list and filters keep working, the background stays
  neutral);
- budget (T-MAP-05): with 50,000 activities, a pan or zoom with the list
  updated within 0.4 s (p95).

## Options

### Map library

1. **MapLibre GL JS** (5.24.0 measured; BSD-3-Clause; WebGL).
   - Pro: actively maintained; clustering built in (supercluster in a worker);
     raster and, later, vector tiles (an offline region as PMTiles needs no
     change of library); smooth zoom.
   - Con: needs WebGL (without a GPU the context cannot be created and the
     map does not start, see the measurements); 1.4 MB of script and a worker file (the CSP build avoids
     blob workers); version 6 is ESM-only, so it cannot be loaded with a plain
     script tag and needs a module-capable server (our scheme could do it,
     untested); the count labels on clusters need glyph files served locally
     (not measured: circles only) or HTML markers; no split/merge animation of
     clusters.
2. **Leaflet 1.9.4 with Leaflet.markercluster 1.5.3** (BSD-2-Clause and MIT;
   canvas renderer).
   - Pro: 150 KB + 34 KB; no GPU needed; numbered cluster icons and the
     split/merge animation are built in, which is exactly the PLAN's
     behaviour; click-to-zoom on a group built in; small API.
   - Con: markercluster has had no release since 2022 (stable, vendored with a
     pinned hash, but nobody to fix bugs for us); cluster icons are DOM
     elements (fine: only the visible ones exist); drawing many tracks is
     slow because the canvas repaints everything; Leaflet 2 is still in
     alpha, so 1.9.x is the end of the line for a long time.
3. **A Python-side map (Folium, static images).**
   - Rejected: no interaction model for clusters and list synchronisation
     without writing the page anyway.

### Tile source

1. **Tracestrack Topo, raster tiles over HTTPS, personal key.**
   - Pro: topographic style useful for outdoor tracks; raster tiles need no
     style engine; one host to allow.
   - Con: needs a key and a network; the host sees the areas viewed; the key
     and the terms of use (quota, caching rules, attribution text) must be read
     again before the release: this is an open point of this draft.
2. **OpenStreetMap standard tiles.**
   - Pro: no key.
   - Con: the project's usage policy discourages heavy and app-embedded use;
     no topographic detail; not a base to build on.
3. **Offline vector region downloaded once (PMTiles/MBTiles).**
   - Pro: no network at view time, no area leak.
   - Con: a large download, a style to design and maintain, regions to
     choose; deferred. Option 1 of the library keeps this door open.
4. **Provider configurable by the user.**
   - Deferred (YAGNI): the URL template and the allowed host live in one
     place so that adding a provider later is a code change in one module.

### Network access of the map page

1. **The page loads tiles itself from `https://tile.tracestrack.com/...`; a
   `QWebEngineUrlRequestInterceptor` blocks every other request and any
   non-HTTPS one; CSP allows `img-src` only for that host.**
   - Pro: simplest; the engine's HTTP cache works as it is.
   - Con: the key is in the tile URL, so the page's JavaScript could read it
     (the page is ours and under CSP; low risk); the host check lives in two
     places (CSP and interceptor).
2. **A tile proxy: the page asks `ponos://app/tiles/z/x/y.png`, a Python handler
   validates integers, adds the key, fetches the tile with timeouts and
   stores it in a cache of its own.**
   - Pro: the page has no network exception at all; the key never reaches the
     page; exact control of the cache size and of what is logged.
   - Con: an HTTP client in the app (a new dependency or `urllib` with care),
     a handler that must answer asynchronously, more code to test.

### Tile cache

1. **The engine's persistent HTTP cache with `setHttpCacheMaximumSize` of
   4 GB, in the operating-system cache folder (not in the profile folder),
   plus a "Clear map cache" command.**
   - Pro: nothing to write; respects the provider's cache headers.
   - Con: eviction is the engine's; the cap is approximate; the files reveal
     the areas viewed to anyone who can read the folder (same exposure as the
     index itself, but outside the profile folder, so a backup of the profile
     does not contain it).
2. **Own cache (only with the proxy of the network option 2).**
   - Pro: exact cap, LRU, inspectable.
   - Con: more code, a durable-data surface to test.

### What the page receives

1. **Start points of the filtered activities as flat arrays (two numbers per
   activity) and, on demand, the simplified tracks of the activities in view
   only, as compact segments (`encode_segments`), at the zoom's tolerance.**
   - Pro: the measured design below; the payload is bounded (50,000 starts =
     0.4 MB as float32; at most a few hundred tracks of at most 2,000 points).
   - Con: the page asks Python for tracks when the view changes, so the
     channel needs a validated message for "tracks of these ids".
2. **All 50,000 tracks loaded in the page.**
   - Rejected by the measurements: 2.3 to 3.4 GB of memory, seconds of first
     draw, pans of 0.3 to 1 s.

## Measurements

Prototype in `experiments/map_prototype/` (RESULTS.md has the environment,
method and all the numbers). 50,000 synthetic activities, 2.5 million track
points, 40 operations (20 jumps to another area and zoom, 20 pans), neutral
background, no network, GPU rendering on one Windows 10 machine; times include
the scan of the 50,000 starts that builds the list. The resolution of a time is
two display frames (about 33 ms).

| Scenario | Library | First draw | Jump p50 / p95 | Pan p50 / p95 | Memory (all processes) |
|---|---|---|---|---|---|
| clustered starts | MapLibre | 0.44 s | 33 / 35 ms | 32 / 34 ms | 676 MB |
| clustered starts | Leaflet | 0.33 s | 34 / 52 ms | 33 / 34 ms | 577 MB |
| starts + tracks in view (design) | MapLibre | 0.45 s | 34 / 39 ms | 33 / 34 ms | 907 MB |
| starts + tracks in view (design) | Leaflet | 0.33 s | 34 / 52 ms | 34 / 35 ms | 751 MB |
| starts + all tracks | MapLibre | 4.4 s | 99 / 484 ms | 33 / 51 ms | 3,394 MB |
| starts + all tracks | Leaflet | 1.3 s | 1,090 / 1,864 ms | 276 / 952 ms | 2,260 MB |

Without a GPU (`--disable-gpu`), MapLibre cannot create its WebGL context and
shows nothing; Leaflet keeps working with a jump p95 of 72 ms and a pan p95 of
50 ms (clustered starts).

Both libraries pass T-MAP-05 by one order of magnitude in the design scenario.
Drawing every track is impossible for both and is not part of the design.

## Decision

Proposed, to confirm when phase 9 starts:

- **Library: Leaflet 1.9.4 with Leaflet.markercluster 1.5.3**, vendored with the
  hash recorded in `docs/dependencies.md`. Reasons: the measured budget is met
  with a wide margin, it needs no GPU, it is the lighter one in memory, and it
  provides out of the box what the PLAN describes (numbered circles, animated
  split and merge, click to zoom). MapLibre GL JS stays the documented way out
  if an offline vector map or rotation is ever wanted; the page-to-Python
  messages are written library-independent so that the swap touches one
  script.
- **Tile source: Tracestrack Topo raster**, key in the operating-system key
  store (a new entry next to the Garmin tokens, same adapter), entered in a
  settings dialog; without a key the map shows the neutral background and a
  line explaining how to add one. A `TILE_URL_TEMPLATE` and the allowed host
  are constants in one module.
- **Network**: option 1 (the page loads the tiles; interceptor and CSP allow
  only `https://tile.tracestrack.com`), because it is the smallest change that
  works; the key is passed to the page by Python after load, never written in
  the HTML. If the review of the terms of use or of the key exposure says
  otherwise, option 2 (proxy) replaces it without changing the rest.
- **Cache**: the engine's HTTP cache, 4 GB cap, in the operating-system cache
  folder, with a "Clear map cache" command in the settings.
- **Page content**: option 1 of "What the page receives". Two messages from the
  page to Python only, both validated: the visible area (four finite numbers,
  the latitudes in [-90, 90]) and the clicked activity (an id that exists in
  the filtered set); Python answers with the list, the totals and the tracks
  of the visible activities.
- **Attribution and User-Agent**: the provider's required attribution is shown
  on the map; the engine's User-Agent is replaced by a generic one for the
  map page (no version of the app, no machine detail).

## Consequences

- The threat model gains the map channel (T-SEC-14) and the network exception
  (T-SEC-15) and declares the accepted risk: the tile host sees the areas
  viewed.
- A new secret (the tile key) in the key store and a new setting; both are
  absent from backups of the profile folder.
- The tile cache is derived and regenerable: deleting it loses nothing.
- Schema v8 (done separately) stores what the search needs without opening
  files: the track box of each activity (four values, antimeridian aware) and
  nothing else; tracks are re-read from the archived file on demand with
  `decode_stored_track`.
- Libraries are updated by changing the pinned version and hash in one script
  and re-running the prototype's measurements.

## How compliance is verified

- T-FILT-01..03 and T-MAP-02, 03, 04, 07 (pure part): `tests/activities/test_filters.py`
  and `tests/activities/test_track.py`; T-MAP-01, 05, 06 on the page and the
  list with the real window in phase 9.
- T-MAP-05 repeated in `nox -s collaudo` with the same scenario as the
  prototype (design scenario, p95 under 0.4 s).
- T-SEC-14: malformed messages (non-numeric or out-of-range coordinates, huge
  payloads, unknown ids) are rejected without effects.
- T-SEC-15: requests to any other host, or over HTTP, are blocked; no GPS
  position in logs or in files outside the profile folder.
- The vendored files match the hashes recorded in `docs/dependencies.md`
  (checked by `experiments/map_prototype/fetch_vendor.py` today, by the build
  in phase 9).
