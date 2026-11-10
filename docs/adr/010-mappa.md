# 010. Map view: library, tile source, cache and page isolation

- Status: Accepted (2026-11-08, interface built: Leaflet and markercluster vendored with
  verified hashes, tile proxy behind `ponos://tiles/`, page without network)
- Date: 2026-11-07
- Amended 2026-11-09: the tile source is now OpenStreetMap, with no key (see the end). The text
  above is the decision as it was taken; where it names Tracestrack or the key, the amendment
  prevails.

## Context

Phase 5b adds the Map view: activities shown as start points grouped in numbered
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

Decided by the project owner on 2026-11-07; the status stays Proposed until the
interface is built and the measurements are repeated on it.

- **Library: Leaflet 1.9.4 with Leaflet.markercluster 1.5.3**, vendored with the
  hash recorded in `docs/dipendenze.md`. No GPU is required, the measured budget
  is met with a wide margin, and numbered circles, animated split and merge and
  click to zoom are built in. MapLibre GL JS stays the documented way out for an
  offline vector map; page-to-Python messages are library-independent.
- **Tile source: Tracestrack Topo raster**; the key lives in the operating-system
  key store (same adapter as the Garmin tokens) and is entered in the settings.
  Without a key, without network or with the credit exhausted the map shows the
  neutral background and a line saying why and what to do.
- **Network: the page has none. Tile proxy in Python.** The page asks
  `ponos://tiles/{z}/{x}/{y}`; a scheme handler in the shell validates z, x, y
  (integers within the limits of the zoom level), downloads
  `https://tile.tracestrack.com/...` with the key from the key store, a timeout
  on every request, no unbounded retry, and a generic User-Agent of the app, and
  answers the page. The CSP and the request interceptor of the page allow only
  `ponos://`. The key is never in the page, in logs, in the profile folder or in
  the cache.
- **Cache: own disk cache with an exact cap and LRU eviction, default 4 GB**, in
  the operating-system cache folder (not in the profile folder), with a "Clear
  map cache" command. Only tiles that were looked at are stored; no prefetch.
- **Page content**: option 1 of "What the page receives". Two messages from the
  page to Python, both validated: the visible area (four finite numbers, latitudes
  in [-90, 90]) and the clicked activity (an id in the filtered set); Python
  answers with the list, the totals and the tracks in view.
- The provider's attribution is shown on the map.

## Consequences

- The threat model gains the map channel (T-SEC-14) and the tile proxy
  (T-SEC-15) and declares the accepted risk: the tile host sees the areas viewed.
  The page itself has no network access to review.
- The shell needs an HTTP client with timeouts (a decision for the dependency
  register: standard library `urllib` or a small library, validated like every
  dependency) and a scheme handler that answers asynchronously without blocking
  the interface thread.
- A new secret (the tile key) in the key store and a new setting, both absent
  from backups of the profile folder.
- The tile cache is a durable-data surface of our own (atomic writes, cap and
  LRU, behaviour on a full disk, stale files) and needs its own tests (T-MAP-11).
  It is derived and regenerable: deleting it loses nothing.
- Schema v8 (separate step, after v7) stores the track box of each activity
  (four values, antimeridian aware) so that "start or track in the area" needs no
  file access; tracks are re-read with `decode_stored_track`.
- Libraries are updated by changing the pinned version and hash in one script
  and re-running the prototype.

## How compliance is verified

- T-FILT-01..03 and T-MAP-02, 03, 04, 07 (pure part): `tests/activities/test_filters.py`
  and `tests/activities/test_track.py`; T-MAP-01, 05, 06 on the page and the
  list with the real window in phase 5b.
- T-MAP-05 repeated in `nox -s collaudo` with the same scenario as the
  prototype (design scenario, p95 under 0.4 s).
- T-SEC-14: malformed messages (non-numeric or out-of-range coordinates, huge
  payloads, unknown ids) are rejected without effects.
- T-SEC-15: the page makes no network request; the proxy calls only
  `https://tile.tracestrack.com` and rejects other hosts, HTTP and out-of-range
  z/x/y; no GPS position or key in logs or in files outside the profile folder.
- T-MAP-11: the cache never exceeds its cap and evicts least recently used tiles.
- The vendored files match the hashes recorded in `docs/dependencies.md`
  (checked by `experiments/map_prototype/fetch_vendor.py` today, by the build
  in phase 5b).

## Amendment 2026-11-09: tile source

Only the tile source and what hangs on it change. The library, the page without network, the tile
proxy, the own cache with an exact cap and what the page receives stay as decided above.

### Context

The owner asked for the standard OpenStreetMap map, without a key to obtain and to type in. The
rejected option 2 of "Tile source" ("OpenStreetMap standard tiles", with the con that the usage
policy discourages heavy and app-embedded use) comes back as the decision.

### Options

1. **Keep Tracestrack Topo with a personal key.**
   - Con: the user must register and paste a key; a secret to store and keep out of logs, backups
     and the page; a quota that can run out; terms read again before every release.
2. **OpenStreetMap standard tiles (`tile.openstreetmap.org`), no key.**
   - Pro: nothing to register; no secret; the key store, its setting page and its messages go
     away.
   - Con: a shared volunteer service with no guarantee; the usage policy limits what an app may
     do and the server can block it without notice; no topographic detail.
3. **Offline vector region.** Still deferred (same reasons as above).

### Decision

Option 2.

- Host `tile.openstreetmap.org`, HTTPS only, path `/{z}/{x}/{y}.png`; the URL is fixed in the code
  (no provider chosen by the user, no setting).
- Usage policy, as code:
  - `User-Agent: PonosCoach/<version> (+<URL of the repository on GitHub>)`;
  - the attribution "© OpenStreetMap contributors" on both maps (the map and the activity map);
  - the cache follows the response headers (`Cache-Control: max-age`, `Expires`), 7 days when they
    say nothing, never more than 30 days;
  - a tile that expired is asked again with a conditional request (`If-None-Match`,
    `If-Modified-Since`) built from the `ETag` and `Last-Modified` stored with it; a 304 renews it;
  - no prefetch and no bulk download: only tiles the page asks for;
  - when the server answers 403 or 429 the app asks nothing for 300 s.
- Tile cache schema 2: validators per tile, no style column. A version 1 file is rebuilt empty
  (the cache is derived: nothing is lost).
- Removed: the Tracestrack key, its store (DPAPI file and Secret Service entry), the key field of
  the Map settings page and the messages about a missing, refused or exhausted key. `keyring`
  stays for the Garmin tokens only.

### Consequences

- Risks that remain and are accepted: the server can block the app without notice, nothing
  promises that the service continues, and the URL is fixed, so a change of source is a code change
  in one module (`platform/tile_http.py` and the composition root). The tile host still sees the
  areas that are looked at and the IP address of the user.
- The threat model drops the secret and the key store (B16 removed), and gains the validators
  taken from the answer (limited in length and characters) and the cool-down on refusal.
- Existing users have an unused key in their key store; the app does not read it any more and
  nothing deletes it.

### How compliance is verified

- T-SEC-15: the proxy calls only `https://tile.openstreetmap.org`, and rejects other hosts, HTTP
  and out-of-range z/x/y; the header of a request carries the `User-Agent` above; odd validators
  are dropped.
- T-MAP-10: a 403 or 429 gives `REFUSED` and a 300 s pause; an expired tile with validators makes
  a conditional request and a 304 renews it.
- T-MAP-11: the cache of schema 2 keeps its cap; a version 1 file is rebuilt empty.
- A test that both maps show the attribution.

## Amendment 2026-11-10: three map backgrounds

Only the tile source changes again, from one fixed server to a closed set of three; everything
else of the two earlier decisions stays (page without network, tile proxy, own cache with an exact
cap, what the page receives).

### Context

The owner asked to change the layer inside the map and then to add OpenTopoMap and CyclOSM, so the
map can be read as a road map, a topographic map or a cycling map.

### Options

1. **Only OpenStreetMap standard.** Con: not what was asked.
2. **A closed set of three backgrounds, each fixed in the code, chosen by the user.**
   - Pro: the choice is a value of the domain, so the page, the proxy, the cache and the tests share
     one definition; no URL ever comes from outside.
   - Con: three more hosts that see the areas looked at; three usage policies to follow.
3. **A background defined by the user (any tile URL).** Rejected: it opens the page, the proxy and
   the privacy of the user to any host (threat model, B15), and a URL is a secret when it carries a
   key.

### Decision

Option 2. The set is `BACKGROUNDS` in `maps/domain.py` (one `MapBackground` value each: stable key,
name, host with its subdomains, path, highest zoom, credit); nothing else defines a host.

| Key | Server (HTTPS, port 443) | Highest zoom | Credit shown on the map |
|---|---|---|---|
| `openstreetmap` (default) | `tile.openstreetmap.org/{z}/{x}/{y}.png` | 19 | © OpenStreetMap contributors |
| `opentopomap` | `{a,b,c}.tile.opentopomap.org/{z}/{x}/{y}.png` | 17 | Map data: © OpenStreetMap contributors, SRTM \| Map style: © OpenTopoMap (CC-BY-SA) |
| `cyclosm` | `{a,b,c}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png` | 20 | CyclOSM \| Map data: © OpenStreetMap contributors |

- The hosts were checked on 2026-11-10 on the pages of the projects (opentopomap.org/about and
  cyclosm.org) and with one tile each. OpenTopoMap and CyclOSM have no bare host that answers, only
  the subdomains; the subdomain of a tile is always the same one (`(x + y) mod 3`), so a tile is not
  fetched from two hosts and the cache of connections of the server is used well.
- The usage rules are the ones of the amendment of 2026-11-09, for all three, in the same code:
  `User-Agent` with name, version and contact URL; the cache follows the response headers (7 days
  when they say nothing, at most 30); a conditional request for an expired tile; no prefetch and no
  bulk download; a pause of 300 s when a server answers 403 or 429, **for that server only** (the
  other two keep working); the PNG, size and time limits of before.
- The page asks `ponos://tiles/{source}/{z}/{x}/{y}`; the handler validates `source` against the
  set and `z` against the highest zoom of that source, so a source that is not in the set, or a
  zoom above its maximum, is refused before the cache or any server is asked.
- Tile cache schema 3: the table is keyed by `(source, z, x, y)`, so a tile of one background never
  serves for another; the cap is one, shared. A file of schema 1 or 2 is rebuilt empty (derived
  data: only time and bandwidth are lost). "Clear map cache" empties all backgrounds.
- The choice is made in Map > Background (Ctrl+Shift+1, 2, 3, exclusive entries) or with a control
  at the top right of the map; both change the same value, which is remembered (`QSettings`,
  `map/background`). The page switches layer without reloading, shows only the credit of the
  background in use and, if its zoom is above the maximum of the new one, comes down to it. A
  problem of a server is shown with the name of the background.

### Consequences

- Risks that remain and are accepted: all three are free services with no guarantee and can block
  the app without notice; OpenTopoMap is sometimes slow (the app waits at most 20 s per tile and
  then shows a plain background); CyclOSM runs on servers of OpenStreetMap France paid for with
  donations, so it deserves the same care (no prefetch, the cache, the pause on refusal). Each
  server sees the areas looked at and the IP address of the user, but only while that background is
  in use. A change of source is still a code change in one module (`maps/domain.py`).
- The choice of a background is a setting like the cache size: no secret, no account.

### How compliance is verified

- T-SEC-15: a source that is not in the set, a zoom above the maximum of the source, a path with a
  query or other segments are refused before any request; each background builds its own URL (host
  with subdomain, path); only the three hosts over HTTPS (plain HTTP only on loopback, in tests).
- T-MAP-10: a refusal of one server does not stop the others; a background with no source is a
  problem, not a crash.
- T-MAP-11: the cache keeps its single cap, separates the sources and rebuilds a schema 2 file.
- `tests/ui/test_map_background.py`: the menu, the control on the map, the page (key, URL, credit,
  highest zoom, no reload), the remembered choice and the notice with the name of the background.
