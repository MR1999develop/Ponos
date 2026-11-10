# Threat model

Amendment 2026-11-10: the app no longer ships a table of foods or example foods (the meals show
energy and macronutrients only). Boundary B11 and test T-NUT-06 no longer exist; the rows that
mention them below are kept as history and say so.

Phase 8 version (2026-11-08): adds the plan and the frozen days (phase 5), the
map and its tile proxy (phase 5b), injuries (phase 6), the Garmin calendar
(phase 7), the profile backup and the release workflow (phase 8). It builds on
that of phase 4 (2026-11-07: the threshold test, the distance series
read from FIT files, the history of the tests in schema v5, threshold
HR and pace, pace zones), on that of phase 4b (2026-11-07: the
nutrition of the day: activity calories, activity level, meal times, goal
periods, the table of foods and the guidance itself), on that of phase 3 (2026-11-06: height, weight and
date of birth as personal health data, dated measurements, sport weights (removed on 2026-11-10: the load is TRIMPeq), HR
zones and activity details), on that of phase 2 (2026-11-06: login in the app, token
store, Garmin responses and downloads, library log and notifications), on that
of phase 1 (2026-11-05, updated on 2026-11-06 with the acceptance defects and
the log boundary) and on phase 0. To be updated again in the same change as
every function that touches a trust boundary. The test identifiers
(`T-SEC-04`…) are those of section 7 of `PLAN.md`.

## 1. What are we building

Local desktop app (Windows and Ubuntu) for one user at a time per profile. It
downloads activities from Garmin Connect with an unofficial login, reads FIT
files, computes the load, shows charts in a local
web page and saves everything in a folder chosen by the user. It has no server
and no listening port.

### Components and trust boundaries

```
                         Internet
   +-----------------------+
   | Garmin Connect        |  (B1) JSON responses and downloaded FIT: untrusted
   +-----------+-----------+
               | HTTPS
 ==============|=========================== app process boundary
   +-----------v-----------+      +--------------------------------------+
   | garmin (adapter)      |      | Import of FIT files and folders      |
   +-----------+-----------+      |  (B2) FIT: untrusted                 |
               |                  +------------------+-------------------+
               v                                     v
   +-------------------------------------------------------+
   | Use cases  ->  Pure domain (load, planning, injury)   |
   +--+-------------+--------------+--------------+--------+
      |             |              |              |
      v             v              v              v
 +----------+  +-----------+  +-----------+  +-----------------+
 | storage  |  | platform  |  | log       |  | ui              |
 | SQLite + |  | keychains,|  | structured|  | Qt + charts:    |
 | FIT      |  | notif.,   |  | (B6)      |  | QWebEngineView  |
 | archive  |  | profile   |  +-----------+  | (B4) web page   |
 +----+-----+  | list (B8) |                 +-----------------+
      |        +-----+-----+
      |              |
 =====|==============|=============== file system boundary
      v              v
 (B3) Profile folder            (B8) App configuration
      chosen by the user:            folder: profiles.json
      ponos.sqlite3 (+ -wal/-shm),   and profiles.json.lock
      archivio/, backup/,
      ponos.lock
      May be on a network or synchronised disk.

 (B7) Distributed package (PyInstaller, SHA256SUMS), build and CI
```

Boundaries:

| Id | Boundary | Data crossing it |
|---|---|---|
| B1 | Garmin Connect → app | JSON responses, downloaded FIT files (zip), error codes, `Retry-After` header, MFA code request |
| B2 | FIT files imported by hand, dragged or from a folder → app | files, names, paths, links |
| B3 | App ↔ profile folder | database, archive, backup, lock, permissions |
| B4 | App → charts web page | chart data as JSON, activity names |
| B5 | App ↔ token store (Windows: file encrypted with DPAPI in `secrets/` in the app's local data folder; Ubuntu: Secret Service) | Garmin tokens, by profile id |
| B6 | App → log file in the user's log folder (`ponos-coach.log`) | events, exception types |
| B7 | Development → user | executable, dependencies, release workflows |
| B8 | App ↔ configuration folder | list of profiles (id, folder, name, last use), lock of the list |
| B9 | User → login dialog → `garminconnect` library | email, password, MFA code (in memory only, in the login thread) |
| B10 | App → system notifications | fixed title and number of new activities |
| B11 | (removed 2026-11-10) Table of foods (`foods.json`, shipped with the app) → parser | values per 100 g, ids, names, portions. It is part of the package, but it is treated as input: a mistake or a change of source must not reach the guidance |
| B12 | FIT `session.total_calories` (inside B1 and B2) → model | calories declared by the device: untrusted until checked against the duration |
| B13 | FIT `record.distance` and `record.speed` (inside B1 and B2) → distance series → threshold test | cumulative distance and its time axis: untrusted until they form a valid series (finite, increasing time, non-decreasing metres) |
| B14 | Map page (`ponos://app/map.html`, a web engine process) ↔ app | five numeric calls from the page (view, click, hover), the list, the totals and the simplified tracks that Python sends to it. Not drawn in the diagram: it sits next to B4 |
| B14b | Activity map page (`ponos://app/track.html`, the same web engine) ← app | one call from Python with the thinned positions of the one activity open in the detail window (at most 2,000 points) and the colours. The page sends nothing back: no `QWebChannel`. Not drawn in the diagram: it sits next to B14 |
| B15 | Tile proxy → the tile server of the background in use: `tile.openstreetmap.org` (default), `{a,b,c}.tile.opentopomap.org` or `{a,b,c}.tile-cyclosm.openstreetmap.fr`; a closed set in the code, no key (ADR 010 amendments of 2026-11-09 and 2026-11-10) | the tile address (source, z/x/y), which reveals the area looked at, the `User-Agent` with the app's name, version and contact URL, the validators (`ETag`, `Last-Modified`) sent back for expired tiles; the PNG and the headers that come back are untrusted |
| B16 | (removed on 2026-11-09) App ↔ tile key store | there is no tile key any more; the number is not reused |
| B17 | App ↔ Garmin calendar (workouts and schedule, unofficial API) | structured workouts sent, the list of scheduled workouts that comes back (untrusted), the local copy of what is deleted |
| B18 | App ↔ backup file (`.ponosbackup`, chosen by the user) | a whole profile in a ZIP: database, original files, manifest. On read it is untrusted input; on write it holds health and GPS data in the clear |
| B19 | Saved plan and frozen days (inside B3) → plan decoder | JSON text and its SHA-256 in the profile database: input, because the file can be edited by hand |

### Assets

| Asset | Why it matters |
|---|---|
| Garmin credentials and tokens | access to the user's account; the password and the MFA code live only in the login thread, the tokens in their store |
| The user's Garmin account | block (429) or flagging for unusual use; modifiable calendar |
| Health data: HR, injuries, pain, doctor's notes | confidentiality |
| Height, weight and date of birth (with the weight history) | personal health data: they are only in the profile database; never in logs, messages or error chains; in plain text like the other profile data (accepted risk, section 5) |
| Threshold HR and threshold pace, with the history of the tests | health data: only in the profile database; never in logs, messages or error chains. They move the zones and the load of every later activity, so their integrity matters too |
| GPS positions of the activities | they reveal home and habits; also the box and the simplified track of each activity (schema v8) and the areas looked at on the map |
| Injuries, pain scores, warning signs, doctor's notes and illnesses (phase 6) | health data: only in the profile database; never in logs, messages, error chains or the saved plan (the plan carries a snapshot without free text) |
| Standing with the tile server | OpenStreetMap tiles are a shared service with a usage policy and no guarantee: too many requests, a missing `User-Agent` or bulk download can get the app blocked for every user (this replaced the Tracestrack key, which is gone with its secret) |
| Local copies of deleted Garmin workouts (`workout_backup`) | they are the only place that holds a definition after Garmin deletes it: integrity matters; they carry names and training structure |
| Backup files (`.ponosbackup`) | one file holds the whole profile in the clear (health data, GPS, injuries); it leaves the profile folder and its permissions |
| Frozen days of the plan (`plan_past_day`) | the history of what was planned: it must not be rewritten |
| Integrity of the profile data (original files, database, parameters) | calculations and plan rely on them; a wrong plan can cause harm |
| Availability of the app and the data | do not lose years of history |
| List of profiles | contains paths and names; if lost, the profiles cannot be found |
| Data outside the profile (other folders of the user) | deleting a profile must not touch them |
| Integrity of the package | the user runs the distributed code |

### Assumptions

- The user's machine and account are not compromised.
- DPAPI (Windows, user scope) and Secret Service (Ubuntu) make the token
  readable by every process of the user: it is not protected from their other
  applications. Credential Manager is not used (limit of 2560 bytes, ADR 006).
- Garmin, Qt and the dependencies are not malicious, but the responses and the
  files that come from outside are untrusted.
- The user chooses the folders and files to import; not all the files in a
  folder are theirs.
- An operating system lock on a local disk works; on a network or synchronised
  disk it is not guaranteed (warning to the user).
- The attacker knows the code (it is public).
- The tile server (OpenStreetMap) is not malicious, but its answers and headers
  are input (PNG checked, validators limited) and it sees which areas the user
  looks at (accepted risk, section 5). It can block the app without notice.
- The Garmin workout and calendar responses have the shape inferred from the
  library, not verified against the live service (`T-MAN-01`).
- A backup file is kept by the user in a place of their choosing; the app
  cannot protect it once it is out of the profile folder.

## 2. What can go wrong (STRIDE per boundary)

S spoofing, T tampering, R repudiation, I information disclosure, D denial of
service, E elevation of privilege.

| Bound. | Threat | Cat. | Test |
|---|---|---|---|
| B1 | Huge, nested JSON response, with wrong types or ids with `../` | T, D | T-SEC-03 |
| B1 | Activity names with HTML or script reach the page or the logs | T, E | T-SEC-03, T-SEC-06, T-SEC-08 |
| B1 | Fake server or network interception | S, I | TLS with full verification (T-SEC-11 for the configuration) |
| B1 | 429, 5xx, timeouts, truncated responses: endless retries, blocked account | D | T-SYNC-03 |
| B1 | Hostile `Retry-After` (negative, NaN, huge, non-ASCII digits) that makes the use case raise or sleep for a long time | D | T-SYNC-03, D-F2-01, D-F2-02 |
| B1 | Expired or revoked token; failed login that saves a partial state | S | T-SYNC-04 |
| B1 | Downloaded FIT that is damaged or hostile | T, D | T-SEC-01, T-SEC-02 |
| B1 | Hostile downloaded ZIP (bomb, no entries, two files, names with `../`) | T, D | T-SEC-02, T-SEC-03, T-SYNC-03 |
| B1 | Download that is another activity than the listed id, recorded under the wrong id | T | T-SYNC-05, D-F2-03 |
| B1 | Listing that slips during the first synchronisation and loses old activities | T | T-SYNC-01, D-F2-04 |
| B1 | Huge response or download read entirely into memory by the library before the cap | D | accepted risk (section 5) |
| B1 | Endless listing (over 1,000,000 entries) | D | no test: open point |
| B1 | The text of a library error (with ids or response fragments) reaches the logs or the user | I | T-SEC-08 |
| B1 | A blocked request cannot be cancelled: synchronisation that does not stop | D | T-SYNC-06; non-interruptible timeouts: accepted risk |
| B9 | Password or MFA code saved, logged or shown (`repr`, log, exceptions) | I | T-SEC-07, T-SEC-08 |
| B9 | Cancelled or failed login that leaves partial tokens; endless wait for the code | T, D | T-SEC-07, T-SYNC-04 |
| B9 | Empty or huge MFA code sent to Garmin; unlimited code attempts | D | T-SYNC-04 (`tests/garmin/test_account.py`) |
| B9 | Empty or huge email or password sent to the library | D | T-SYNC-04 (`tests/garmin/test_account.py`) |
| B2 | FIT with huge declared sizes, endless records, false extension | D | T-SEC-01, T-SEC-02 |
| B2 | FIT with absurd start or end (outside 1990–2099 UTC) that pollute archive, table and calculations | T | T-SEC-02 |
| B2 | Hostile GPX, ZIP or XML files (XXE, billion laughs, decompression bombs, names with `../`) chosen by hand or in a folder: since phase 8 the import never opens them (a folder ignores them, a hand choice reports them as an unsupported format); there is no XML parser any more | I, D, T, E | T-SEC-02, T-IMP-04 |
| B2 | Symlinks, junctions, reparse points, Windows reserved names, long paths in the scan of a folder | T, I, E | T-SEC-04 |
| B2 | File replaced by a link between the check and the read | T, I | T-SEC-04 |
| B2 | Huge folder (whole disk) | D | T-IMP-02, T-PERF-02 |
| B2 | Duplicates or altered files that change the past | T | T-IMP-01, T-IMP-03, T-SYNC-05 |
| B3 | Migration v11 that removes the GPX activities: an altered archive path in the database that points outside the archive, a link in place of a file, an interrupted migration | T, I, D | T-DATI-01 (`tests/collaudo/test_fase8_solo_fit.py`) |
| B2 | Unexpected error on a file that stops the import or leaves half of the files | D | T-IMP-01, T-UI-08 |
| B3 | Interruption midway through a write: record without file, orphan temporary files | T, D | T-DATI-02 |
| B3 | Archive file altered or missing after the import | T | T-DATI-03 |
| B3 | Two instances on the same profile; network or synchronised disk | T, D | T-DATI-04, T-SEC-09 |
| B3 | Lock file replaced by a link | T, E | T-DATI-04, T-SEC-04 |
| B3 | Other users of the PC read the folder, the archive or the copies in `backup/` | I | T-SEC-09 |
| B3 | Profile folder created with wide permissions (inherited DACL, file system that ignores modes) | I | T-SEC-09 |
| B3 | SQL injection from names and notes | T | T-SEC-05 |
| B3 | Database of an old or unknown version; migration that damages it | T | T-DATI-01 |
| B3 | Migration v2 → v3 interrupted midway or done by two instances together | T, D | T-DATI-01 |
| B3 | Height or date of birth modified or deleted after creation (bug or direct SQL) | T | T-DATI-01 (trigger), T-QUAL-02 |
| B3 | Weight, height or date of birth out of range, with non-ASCII digits, NaN, exponents or huge text (user input or altered profile file) | T, D | T-QUAL-02, T-DATI-01 |
| B3 | Measurement with a validity day in the future, before 1900-01-01 or that brings inconsistent HR | T | T-QUAL-02 |
| B3 | Weight of a sport that is NaN, infinite or outside 0-5 that zeroes or inflates the load | T | T-QUAL-02, D-F3-02 |
| B3 | Load of a previous version mixed with the new ones after a change of parameters, zones or model (TRIMPeq, no weights) | T | T-CALC-05, T-CALC-01 |
| B3 | Height, weight, date of birth or HR in logs, messages, error chains | I | T-SEC-08 |
| B3 | Database of a future version rewritten by an old version | T | T-DATI-01 |
| B3 | Deleting a profile folder that leads outside (link, junction) or deletes user files not created by the app | T, E | T-SEC-04, T-UI-03 |
| B3 | Deleting the folder of a profile that is open elsewhere | T, D | T-DATI-04 |
| B3 | Interrupted creation of a profile: half folders and files, or deletion of folders that were already there | T, D | T-DATI-06 |
| B3 | Data loss without a usable backup | D | T-DATI-05 |
| B4 | The page makes network requests or loads remote images | I | T-SEC-06 |
| B4 | Script in the data executed in the page; bridge to the app code | E | T-SEC-06 |
| B4 | SVG/PNG export with unwanted content | I | T-UI-04 |
| B4 | Phase 3 charts (weekly load, zones, activity HR) with unchecked names or texts in the page | T, E | T-SEC-06, T-UI-04 |
| B3 | Archive file gone or altered while the detail is open or while calculating | T | T-DATI-03, scenarios of `tests/collaudo/test_fase3_scenari.py` |
| B3 | Parameters (minimum and maximum HR, thresholds) changed during a synchronisation: loads that do not match the calculation from scratch (before 2026-11-10 the sport weights too) | T | T-CALC-05, `tests/collaudo/test_fase3_scenari.py` |
| B5 | Token in the folder, in the configuration, in the logs, in temporary files, in backups | I | T-SEC-07 |
| B5 | Token not deleted after logout | I | T-SEC-07 |
| B5 | Another process of the user reads the token | I | accepted risk (assumptions) |
| B5 | The file of one profile copied to another is decrypted or the tokens of one profile are used for another | S, I | T-SYNC-04 |
| B5 | Token file altered, truncated, huge or of unknown format | T, D | T-SYNC-04 |
| B5 | Silent fallback to a plain file when Secret Service is missing | I | T-SEC-07 (`TokenStore` contract) |
| B5 | Tokens that are not a JSON object read by the library as a file path | T, E | T-SYNC-04 |
| B10 | Personal data in the notification text; notification that blocks the UI or brings down the synchronisation | I, D | T-SEC-08, `tests/platform/test_notifier.py` |
| B6 | Health data, passwords or unchecked input in the logs | I | T-SEC-08 |
| B6 | Paths, file names or GPS positions in the logs | I | T-SEC-08 |
| B6 | Injection of log lines with control characters | T, R | T-SEC-08 |
| B6 | Text of an exception (with paths or names) written in the log | I | T-SEC-08 |
| B6 | Log that grows endlessly in the user's folder | D | T-SEC-08 (rotation) |
| B6 | Unhandled exception in a thread: no trace or details to the user | I, R | T-SEC-08, T-UI-08 |
| B6 | Unexpected exception shows details or paths to the user | I | T-UI-08 |
| B7 | Vulnerable dependency or one with a forbidden licence | T, E | T-SEC-11 |
| B7 | Package with local paths, user name, development files | I | T-SEC-12 |
| B7 | Package altered after publication | T | T-SEC-12 (hash in `SHA256SUMS`) |
| B7 | DLL loaded from an untrusted folder (Windows) | E | T-SEC-12 |
| B7 | CI workflow with excessive permissions or interpolated input | E | T-SEC-11 |
| B8 | List of profiles unreadable, too large, corrupt or of a future version, overwritten | T, D | T-DATI-01 |
| B8 | Two instances that update the list together and lose changes | T | T-DATI-04 |
| B8 | List entry pointing to a folder with another profile or with no profile | S, T | T-DATI-04, T-UI-01 |
| B8 | Paths and names of profiles (personal data) in the logs | I | T-SEC-08 |
| B11 (removed) | Food table too large, too deeply nested, with duplicate ids, out-of-range or non-finite values, energy not matching the macros, too few or too many entries | T, D | T-NUT-06, D-F4B-06 |
| B11 (removed) | Food source and licence not recorded; values never checked against the source | T | T-NUT-06, D-F4B-08; accepted risk (section 5) |
| B12 | Declared calories absurd for the duration (50,000 kcal in 20 minutes) that inflate the expenditure and the target | T | T-NUT-03, D-F4B-02 |
| B12 | Zero or invalid FIT calories read as real | T | T-NUT-03 |
| B3 | Migration v3 → v4 interrupted midway, done by two instances, or without a copy | T, D | T-DATI-01 |
| B3 | Overlapping goal periods, also by an `UPDATE` of the dates; level, meal times or pace out of range | T | T-DATI-01, T-UI-01 |
| B3 | Weight, age, HR, level, goals and meal times in logs, messages or error chains | I | T-SEC-08 (`tests/collaudo/test_fase4b_log_salute.py`) |
| B3 | Activity indexed before v4 without calories that makes the day wrong | T | T-NUT-03, D-F4B-09 |
| B13 | Distance that is NaN or infinite (FIT float field, speed) that breaks the window search or gives a nonsense pace | T, D | T-PIANO-06, D-F4 |
| B13 | Distance that goes backwards (damaged file, odometer reset) read as a valid effort | T | T-PIANO-06, D-F4 |
| B13 | Jumps of distance (GPS glitch, a record after a long gap) that make a window look like a world-record effort and set a wrong threshold pace | T | T-PIANO-06 (steps above 12 m/s ignored for the window; implausible window falls back to heart rate, no pace), D-F4-04 |
| B13 | Gaps in the records (pauses, missing distance) hidden by a distance that keeps growing, so a test with long stops is accepted | T | T-PIANO-06 (pauses over 60 s in the segment, HR and distance records together), D-F4 |
| B13 | Speed integrated over a gap that invents distance | T | T-PIANO-06 (no distance over gaps above 10 s), D-F4 |
| B13 | Series with millions of points or unsorted times that exhaust memory or time in the window search | D | T-SEC-01, T-SEC-02, T-PERF-02 (limits of the readers; the search tries only the breakpoints) |
| B13 | Duplicate or backward timestamps that reorder the distance | T | T-PIANO-06 (samples not later than all the previous ones are dropped, as for HR), D-F4 |
| B3 | Migration v4 → v5 interrupted midway, done by two instances, or without a copy | T, D | T-DATI-01 |
| B3 | History of the tests with an absurd threshold or pace, a day in the future, a key that is not an activity key (altered file or direct SQL) | T | T-DATI-01 (`CHECK` constraints), T-PIANO-06 |
| B3 | History of the tests lost when the activity index is rebuilt | T | T-PIANO-06 (no foreign key; contract test of the history) |
| B3 | Applying a test half done: history without the threshold, or the reverse; undo that leaves the profile in another state | T | T-PIANO-06 (one transaction; exact undo; undo refused if the entry was changed) |
| B3 | Threshold HR, threshold pace, test days and the history in logs, messages or error chains | I | T-SEC-08 (`tests/collaudo/` log of the health data) |
| Plan | Test taken for another activity (a race, an interval session) that moves the zones | T | T-PIANO-06 (the user chooses the activity, running only; refusals with reasons; undo) |
| Plan | A threshold applied by mistake changes the load of past activities | T | T-PIANO-06 (undo), T-CALC-05 (fingerprint of the parameters) |
| Plan | Guidance that harms: deficit too large, intake under the minimum, low energy availability, heavy days, low body mass index | T | T-NUT-02, D-F4B-01 |
| Plan | Numbers given to a minor | T | T-NUT-02, T-UI-01 |
| Plan | Guidance taken as medical advice or a diagnosis | T | fixed notice always on screen (`tests/ui/test_day_window.py`); no diagnosis in the model |
| Plan | Absurd date or person (age over 130, far-future day, extreme weight and height) that crashes the day | D | T-NUT-01, D-F4B-05 |
| Plan | Time of the recovery wrong across a change of daylight saving time or after long stops | T | T-NUT-04, D-F4B-04, D-F4B-07 |
| Plan | Dangerous plan from wrong data or wrong rules | T | T-PIANO-02, T-PIANO-03, T-PIANO-04, T-INF-01, T-INF-02, T-INF-03 |
| B19 | Saved plan edited by hand, with a good checksum and impossible numbers (negative minutes or load, NaN) that break the nutrition or the calendar | T, D | T-SEC-02, D-F5-01 (`tests/collaudo/test_fase5_piano_manomesso.py`) |
| B19 | Saved plan damaged, truncated, huge or nested: exception, or a plan that cannot be read for ever | T, D | T-SEC-02 (unreadable, never an exception; the next rebuild writes a good plan) |
| B19 | Frozen day edited or deleted (the past rewritten); a damaged frozen day dropped so that the adherence looks better | T, R | T-PIANO-04 (the database refuses `UPDATE` and `DELETE`; a damaged day is shown as unreadable, not dropped) |
| Plan | Two calls that build the plan together, or a rebuild killed halfway, leave a frozen day without its plan or the reverse | T | T-DATI-02 (`tests/collaudo/test_fase5_annulla_interruzioni.py`) |
| Plan | Goals, availability or session types changed and undone: the undo does not restore the exact state | T | T-DATI-02 (command killed at every statement; undo exact) |
| Plan | Nutrition of a future day that harms: deficit in the week of a race, a made-up session time | T | T-NUT-07, T-NUT-08 (ADR 011) |
| Plan | Refuelling that falls at the minute the next session starts | T | T-NUT-04, D-F5-03 |
| B14 | The map page calls something other than its five slots, or sends NaN, out-of-range views, an activity that Python did not list | E, T | T-SEC-14 (`tests/collaudo/test_fase5b_canale.py`) |
| B14 | The page deletes its own bridge (`deleteLater`) | D | accepted risk (section 4, map page): no other effect |
| B14 | The map page makes a network request or loads a remote image or script | I | T-SEC-15 (CSP, interceptor that lets only `ponos://` pass) |
| B14 | Messages about an older list applied to the new one | T | T-SEC-14 (dropped and counted as stale) |
| B15 | A tile address with Unicode digits, a query or a port passes the check and reaches the provider | T, I | T-SEC-15, D-F5B-01 |
| B15 | Hostile tile answer: not a PNG, huge, huge in pixels, slow drip, lying length, redirect to another host | T, D | T-SEC-15, T-MAP-10 (cap of 1 MiB, 1,024 px, total deadline of 20 s, redirects never followed) |
| B15 | 403 or 429 (refused), no network, 5xx: endless retries, hammering the provider | D | T-MAP-10 (one attempt per call, cool-down per problem, 300 s when the server refuses; expired tiles asked with a conditional request; the map stays usable on a neutral background) |
| B15 | Validators from the answer (`ETag`, `Last-Modified`) with odd characters or a huge length stored and sent back (header injection, cache bloat) | T, D | `_VALIDATOR`: printable ASCII, 1 to 256 characters, otherwise dropped; the cache `CHECK` repeats the length limit; tests of `HttpTileSource` and of the cache |
| B15 | The `User-Agent` carries the contact URL: it is fixed in the code and checked (printable ASCII, at most 128 characters); it holds no profile data, no user name, no path | I | `tests/platform/` (the header of a request) |
| B15 | One missing tile (404) silences all the tiles around it | D | T-MAP-10, D-F5B-02 |
| B15 | The provider sees which areas are looked at | I | accepted risk (section 5), declared by the user |
| B15 | Plain HTTP or another host used for tiles | T, I | T-SEC-15 (HTTPS and the three hosts only, fixed in the code; plain HTTP only on loopback, for tests) |
| B15 | A source or a host that the page or a setting makes up (`ponos://tiles/evil/...`, a saved value that is a URL) | T, I | T-SEC-15 (the source is looked up in the closed set `BACKGROUNDS`: unknown, wrong case, a host or a URL are refused before the cache and the network; an unknown saved value is the default; the page can only name a key, through a slot that validates it) |
| B15 | A tile of one background served for another (a cache poisoned by mixing sources), or a refusal of one server silencing the others | T, D | T-MAP-10, T-MAP-11 (the cache key includes the source; the cool-down is per source) |
| B15 | The server blocks the app (the URL is fixed, there is no second source) | D | accepted risk (section 5); the map is a convenience and keeps a neutral background |
| B16 | Removed on 2026-11-09 with the tile key: no secret, no key store, no fallback to a file | - | the tests of the key and of its store were removed with the code |
| Map | Tile cache over its cap, WAL that grows beyond it, stale tiles forever, a full disk | D | T-MAP-11, D-F5B-03 |
| Map | The areas looked at stay in the cache folder, readable by every process of the user | I | accepted risk (section 5); outside the profile and its backups; "Clear map cache" |
| Map | GPS positions in logs or in `QSettings` | I | T-SEC-08, T-SEC-15 |
| Injury | The plan keeps loading a zone with a warning sign, or an illness stays ignored | T | T-INF-01, T-INF-03 |
| Injury | Doctor's notes and pain scores copied into the saved plan, a log or an error | I | T-SEC-08 (`tests/collaudo/test_fase6_log_salute.py`); the plan carries a snapshot without free text (ADR 012) |
| Injury | The ramp returns to +5 too early (closing dated ahead, review not done) | T | T-INF-03, D-F6-01 |
| Injury | The app decides the return by itself | T | T-INF-02 (`ready_to_close` is information; closing is the user's) |
| B3 | Migration v9 → v10 (injuries) or v6 → v7 → v8 interrupted midway or done by two instances | T, D | T-DATI-01 (`tests/collaudo/test_fase6_migrazione_v10.py`, `tests/collaudo/test_fase5b_migrazione_v8.py`) |
| B17 | A hostile or damaged calendar response: wrong types, names with control or invisible characters, entries without a date | T, E | T-SEC-03, T-SEC-10, D-F7-03 |
| B17 | Deleting a workout that is not the one chosen, an entry changed since, a day in the past, or more than the cap | T | T-SEC-10 (copy first; stale entry refused; past days never; cap of 100) |
| B17 | A deletion refused by Garmin leaves a copy that "create again" turns into a duplicate | T | T-SEC-10, D-F7-01 |
| B17 | A send repeated or cut by a lost answer creates duplicate workouts | T | T-SYNC-03 (same name on the same day is not created again; the calendar is read before creating) |
| B17 | A long plan can only be sent a week at a time | D | D-F7-02 (the cap of 60 counts only sessions not yet on the calendar) |
| B17 | 429, 5xx, expired session, cancellation during a send or a deletion | D | T-SYNC-03, T-SYNC-04 (same policy as the synchronisation, bounded attempts) |
| B17 | A copy altered in the database is created again; a hostile copy reaches Garmin | T | T-SEC-10 (SHA-256 of the definition checked; refused without a request) |
| B17 | The shape of the workout and calendar responses differs on the live service | T | accepted risk (section 5); `T-MAN-01` is the check |
| B3 | Workout copies that grow without limit | D | ADR 013 (restored copies forgotten after 30 days, at most 1,000 wait) |
| B18 | Backup that overwrites a file, lands inside the profile folder or follows a junction in the archive | T, E | T-DATI-05 (exclusive rename; path compared after resolving links; junction not followed) |
| B18 | Backup that holds the Garmin tokens, the lock file, the pre-migration copies or an unknown file | I | T-SEC-07, T-DATI-05 (closed list of entries) |
| B18 | Hostile backup: bomb, too many entries, false sizes, names with `../`, absolute or Unicode-digit names, encrypted entries, a manifest that lies, a database that is not a profile | T, D, E | T-SEC-02, D-F8-04 (`tests/collaudo/test_fase8_zip_ostili.py`) |
| B18 | A restore over an existing profile, into a folder with files, or that leaves a folder that looks like a profile after a crash | T | T-DATI-05, T-DATI-02, D-F8-01, D-F8-03 |
| B18 | A backup or restore killed halfway leaves a partial copy of the user's data in the chosen folder | I, D | T-DATI-02, D-F8-02 |
| B18 | The backup file read by other users of the PC or copied by a cloud client | I | accepted risk (section 5): not encrypted; private permissions where the file system has them |
| B18 | A backup of a newer version restored by an old app | T | T-DATI-01 (`BackupTooNewError`) |
| B7 | Release workflow with excessive permissions, an unpinned action, a token with a long life | E | T-SEC-11 (read-only token; only the attestation job has `id-token` and `attestations`; actions pinned to a SHA; the release is a draft: publishing is manual) |
| B7 | Packaged executable with local paths, user name or development files | I, D | T-SEC-12 (`tests/collaudo/test_fase8_pacchetto.py`) |
| B7 | Package replaced after publication | T | T-SEC-12 (`SHA256SUMS` checked in the workflow, provenance attested, SBOM attested); the executable is not signed (exception 2, section 5) |
| UI | Interface blocked by a long operation | D | T-UI-02, T-SYNC-06 |

## 3. What we do

- External inputs (JSON, FIT, ZIPs downloaded from Garmin, file names) become domain types at the
  boundary, with limits checked before reading or allocating; order: origin,
  size, characters, syntax, semantics. We reject, we do not repair. One single
  point decodes FIT files, with a single error.
- Reduced surface (phase 8): only `.fit` files are imported. There is no XML parser
  (`defusedxml` was removed from the dependencies) and no reading of ZIP files chosen
  by the user; GPX and ZIP files in a folder are ignored, and chosen by hand are
  reported as an unsupported format, without opening them. The extension decides
  whether a file is tried; the FIT reader checks the rest (signature, CRC, structure,
  limits). A start or an end (start plus elapsed time) outside [1990-01-01,
  2100-01-01) UTC makes the file damaged.
- ZIP (only the download from Garmin and the profile backup): central directory read with a cap, number of entries, sum of sizes and
  compression ratio checked before extracting; extraction in memory in blocks
  with the real size checked again and the CRC verified; entry names never
  become paths; nested ZIPs and encrypted entries rejected; duplicate names
  (even only by letter case) rejected.
- Scan of a folder: only regular files inside the chosen folder; links,
  junctions, reparse points and special files skipped and reported; the chain
  of folders is checked again at read time and the file type on the open
  handle; Windows reserved names and long paths reported. Caps declared in a
  single module; source folder never modified.
- An unexpected error on a file is a failed result for that file; the import
  continues and the message does not contain the exception.
- The names of the archive files (`archivio/<year>/<sha256>.fit`) are
  generated by the program; no external data enters a path. Format in ADR 005.
- SQL always parametrised; static check against composed SQL. `CHECK`
  constraints, foreign keys and `STRICT` in the database.
- Files written to an exclusively created temporary file, `fsync`, durable
  rename, all in one shared module; record after the file; SHA-256 and size
  verified at every read. An altered or missing file is reported and is put
  right by importing the original again.
- Database: `quick_check` and `foreign_key_check` at opening; future version
  rejected without touching it; a file without the tables of a profile is
  damaged even if its `user_version` matches; safety copy in
  `backup/ponos-v{from}-v{to}.sqlite3` before every migration (without a copy
  it does not migrate); the preview of a profile (deletion confirmation) opens
  it read-only, without migrating or creating files next to it.
- Profile folder: created private (Windows: DACL protected with only the user
  and SYSTEM; Ubuntu: 0700, verified after creation, error if the file system
  ignores it), files created exclusively, `archivio/` and `backup/` with the
  same permissions, no link followed.
- Lock of a profile: operating system lock on `ponos.lock`, held for the whole
  life of the profile; it frees itself if the process dies; the file cannot be
  a link. The lock is taken before opening the database.
- List of profiles (`profiles.json` in the configuration folder): versioned
  format, cap of 1 MiB, unknown fields kept, atomic write under a system lock
  with bounded wait; if it is unreadable, corrupt or of a future version it is
  an error and is not overwritten.
- Deleting a profile: refused for the open profile (the operation itself takes
  the lock); limited to the names the profile owns (database and `-wal`,
  `-shm` and `-journal` files, `archivio/`, `backup/`, lock); refused if there
  is anything else or if the tree contains a link or a junction; confirmation
  with friction that shows the folder and the number of activities.
- Creating a profile: if a step fails, the rollback undoes only what the
  creation did; pre-existing folders and files are not touched, nor is the
  profile of another instance that won the race on the same folder
  (T-DATI-06).
- Charts page: only local content, network blocked by an interceptor, data as
  encoded JSON, no `QWebChannel` (the map page has one, with five numeric
  slots: section "The map page").
- Garmin login in the app (exception written in `CLAUDE.md`, ADR 002). The
  password and the MFA code go only to `garminconnect`, are not saved and do
  not appear in `repr`, messages or logs; empty or huge email and password are
  rejected before contacting Garmin; the empty or huge MFA code is not sent,
  the code attempts are at most 3 and the wait for the code has a cap of 5
  minutes. A cancelled or failed login saves nothing.
- Tokens (ADR 006): on Windows one file per profile encrypted with DPAPI (user
  scope) in a private folder outside the profile, with entropy tied to the app
  and to the profile id, versioned format and reading with a size cap; on
  Ubuntu the Secret Service, and no plain fallback if it is missing. Never in
  the profile folder or in the backup. An unreadable or altered file asks for
  a new login. Logout deletes the tokens. On resume, tokens that are not a
  JSON object do not reach the library (it might read them as a path). Tokens
  renewed by the library are saved again.
- Garmin responses (B1): the listing becomes domain types at the boundary
  (plain numeric ids, plausible times and durations, plain sport key); a
  malformed entry is discarded, a page over the cap or not interpretable is a
  definitive error; no free text from Garmin (names, descriptions) enters the
  domain. The declared total serves only the progress.
- Garmin download: cap of 50 MB on the ZIP; the original is extracted with the
  limits of the ZIP adapter and goes through the same checks as imported
  files; it must match the listed entry (start, duration) or the entry fails
  and nothing is recorded. The archive is written before the record
  transaction.
- Few requests to Garmin, pauses with jitter, backoff with a cap and a maximum
  number of attempts; the library retries zero times. `Retry-After` is read
  only if it is a number with ASCII digits; an unreadable value is ignored, an
  implausible one stops the synchronisation. After 3 entries in a row with
  Garmin unreachable it stops without consuming the attempts of the entries.
- Library log: `garminconnect` at critical level (at error level it records
  the request path, with the activity id, and the text of the errors);
  `requests`, `urllib3`, `curl_cffi` at warning. In our synchronisation error
  logs only the type.
- Notifications: fixed title and number of new activities, never names or
  data; they run in a thread of their own, one at a time, and if they fail a
  record with only the type remains.
- Synchronisation on a thread with a connection of its own; cooperative
  cancellation checked before every request, entry and page, with
  interruptible pauses.
- Logging only in the shell (`ui/diagnostics.py`, configured by the
  Composition Root). Never health data, tokens, passwords, paths, file names
  or GPS positions. User messages are born in one single place and do not
  carry the text of exceptions. The detail of a rejected file is an enumerated
  reason, not its content.
  - Log boundary: a rotating file (1 MB, three copies) in the user's log
    folder (`platformdirs`); if the folder cannot be used the app starts and
    the logs go to stderr.
  - A single formatter produces the text: for an exception it writes the chain
    of types (`A <- B`), never the text or the traceback, and replaces control
    characters with their written form, so a hostile name cannot fabricate
    false lines. Times in UTC.
  - Last-resort handler for the main thread and for the other threads: it
    records only the types, warns the user with a generic message and never
    stops the app, even if the warning fails. Ctrl+C passes through.
  - Tests: `tests/ui/test_diagnostics.py`, `tests/collaudo/test_interfaccia.py`
    (T-SEC-08, D-F1-05), `tests/ui/test_window_lifecycle.py`.
  - From the Help menu the user opens the log folder (Ctrl+Shift+L). The file
    is readable by every process of the user: for this reason it does not
    contain personal data.
- Single error handler with a generic message.
- Dependencies: lockfile, blocking `pip-audit`, licences checked; CI with
  minimal permissions and actions pinned to a SHA; `SHA256SUMS` for the
  packages.
- Personal body data (phase 3, ADR 007): height, weight and date of birth
  become domain types valid by construction (`Height`, `Weight`, `BirthDate`)
  and their exceptions do not repeat the rejected value. The weight is an
  integer in tenths of a kilogram; the text is read with a strict grammar
  (length first, only ASCII digits, one decimal separator; exponents, `NaN`,
  `_` and full-width digits rejected, never reinterpreted). The database
  repeats the limits with `CHECK`; height and date of birth have a trigger that
  rejects `UPDATE` and `DELETE`. The validity day of a measurement is not in
  the future or before 1900-01-01; the constraint between the HR values is
  checked on the result. None of these values enters a log, a message or the
  chain of an error (T-SEC-08); the interface shows problems with enumerated
  categories, not with the value.
- Measurements and fixed data (and, before 2026-11-10, sport weights) are written in a single
  transaction (all or nothing); the v3 migration goes through the safety copy
  and is tested interrupted at every statement (T-DATI-01). The load cache is
  derived: the migration empties it and the fingerprint includes parameters,
  weight of the sport, zone percentages and model version, so no old load stays
  mixed with the new ones (T-CALC-05).
- Zones and detail: the seconds in zone come from the same samples as the load
  (discards and gaps included); the detail re-reads the original from the
  archive with the same size and SHA-256 checks, and a missing or altered file
  is a result with the reason, never used silently. The figures are built by
  pure functions and go through the same local page, without network, as the
  previous charts.
- Deterministic plan with rigid ramp limits and injury warning signs; no
  diagnosis.
- Nutrition (phase 4b, ADR 008):
  - Food table (B11): removed on 2026-11-10 together with the table (ADR 008, amendment).
  - Declared calories (B12): zero and the FIT invalid value mean "not
    computed"; above 50,000 kcal, or above 1,500 kcal per hour of moving time,
    they are discarded (not clipped) and estimated. With several sessions in a
    file, they count only if all declare them.
  - Health data: weight, age, HR, level, goals and meal times stay in the
    profile database. The nutrition use cases and the day page write nothing
    to the log about the person; a failure shows a generic message without the
    text of the exception (T-SEC-08).
  - Harmful advice: the safety limits of the deficit are in the pure domain
    and the most restrictive wins: a weekly loss of at most 1% of the body
    weight, at most 20% of the expenditure and 750 kcal, never under the basal
    rate nor 1,200 kcal (women) or 1,500 kcal (men), an energy availability of
    at least 30 kcal per kg of fat-free mass (estimated with Boer 1984), less
    or none on heavy days (ADR 008); no numbers under 18
    years, no deficit with a body mass index under 18.5 (with a warning to see
    a professional); an age over 130 years is "unavailable", not an error;
    the page carries a fixed notice that cannot be hidden. The model gives no
    diagnosis.
  - Schema v4 goes through the same safety copy as the others
    (`backup/ponos-v3-v4.sqlite3`), is tested interrupted at every statement,
    and the database rejects overlapping goal periods (triggers on `INSERT`
    and `UPDATE`) and values outside the limits (`CHECK`).
  - The day is a query: it saves nothing and does not touch the index of the
    activities.
- Threshold test (phase 4, ADR 009):
  - Distance series (B13): built by the file readers, which already apply the
    caps on size, records and time (T-SEC-01, T-SEC-02). Times that are not
    later than all the previous ones are dropped, as for the HR. A series with
    a value that is not finite or with metres that go backwards is not used and
    not repaired: the activity stays valid, without pace. Speed is integrated
    without inventing distance over gaps above 10 s. `DistanceSeries` checks its own invariants
    (finite, strictly increasing time, non-negative non-decreasing metres).
  - Evaluation: pure and deterministic. The window search tries only the
    breakpoints of the series (no loop per second). A pace outside
    90–3,600 s/km is left out; pauses over 60 s in the segment, valid HR under
    90% of the last 20 minutes and a threshold outside the profile limits
    refuse the test, always with an enumerated reason and never repaired.
  - The activity is chosen by the user and must be a run; the test is applied
    only on request. The file is read from the archive with the usual size and
    SHA-256 checks; an unreadable file is an excluded activity.
  - Apply and undo in one transaction, through the rules of the dated
    measurements; the undo restores the exact previous state and is refused if
    the entry was changed in the meantime.
  - Schema v5 (`threshold_test`) goes through the same safety copy as the
    others (`backup/ponos-v4-v5.sqlite3`), is tested interrupted at every
    statement, and the database repeats the limits with `CHECK` (activity key
    prefix and length, valid ISO day from 1900-01-01, threshold 40–250 bpm,
    pace 90–3,600 s/km or NULL). It has no foreign key to the index, so the
    history survives a rebuild.
  - Health data: threshold HR and pace and the days of the tests stay in the
    profile database. The assessment use cases write nothing to the log about
    the person; a failure shows a generic message without the text of the
    exception (T-SEC-08).

- Plan and frozen days (phase 5, ADR 011):
  - The saved plan and each frozen day are input (B19): text, size cap and
    SHA-256 checked, then the content is decoded into types that are valid by
    construction (`PlannedSession`, `PlannedRace`, `PlanDay` refuse minutes
    under 1, negative or non-finite loads, an empty goal id). Anything else is
    "unreadable", never an exception; the next rebuild writes a good plan.
  - A frozen day is never rewritten: the database refuses `UPDATE` and
    `DELETE` (trigger), there is no command to clear the history, and a damaged
    day is shown in the adherence as unreadable, not dropped and not replaced by
    the current plan. The only way to remove it is to delete the profile.
  - The plan is rebuilt on the data thread after each overview, in one
    transaction with the freezing; the undo of goals, availability and session
    types restores the exact state. The nutrition of the days ahead is built
    from the saved plan with the same model, with no deficit in the week of an
    A or B race and in the taper before it.
- Map (phase 5b, ADR 010; the rules of the page are in the section "The map
  page"):
  - Page without network: only `ponos://` passes the interceptor. Tiles come
    through `TileProxy` (Python): the address is parsed with ASCII digits only
    and checked against the pyramid, the request goes to one host over HTTPS
    with full verification, one attempt per call, a socket timeout and a total
    deadline, redirects never followed, no system proxy, an answer that must be
    a PNG within 1 MiB and 1,024 px. A problem sets a cool-down (15 s for a
    network problem or a bad answer, 300 s when the server refuses with 403 or
    429); a bad answer (a 404) cools down that tile only.
  - Source (amendment of 2026-11-09): `tile.openstreetmap.org`, no key and so
    no secret. The usage policy is met in code: `User-Agent`
    `PonosCoach/<version> (+<contact URL>)`; the cache follows the response
    headers (7 days when they say nothing, at most 30 days); an expired tile is
    asked again with `If-None-Match` or `If-Modified-Since` built from the
    validators stored with it; no prefetch and no bulk download. The validators
    come from the answer, so they are limited (printable ASCII, 1 to 256
    characters) before they are stored or sent back.
  - Tile cache: own, on disk, in the user's cache folder, with an exact cap
    (default 4 GB), LRU eviction, the WAL truncated after every write, "Clear
    map cache". Only tiles that were looked at are stored. Schema 3 keys a tile
    by background, zoom, column and row (one cap for all); a file of version 1
    or 2 is rebuilt empty.
  - Three backgrounds (amendment of 2026-11-10): OpenStreetMap, OpenTopoMap
    and CyclOSM, each a value of the domain with its host, subdomains, path,
    highest zoom and credit; the same usage rules for all, a cool-down per
    server. The user chooses one (menu or control on the map, remembered); the
    page can only name a key of the set.
  - Without network or when the server refuses, the map shows a neutral
    background and a line that says why; the tracks, the list and the filters
    keep working.
  - Positions: the box and the simplified track of each activity are in the
    profile database (schema v8, `CHECK` constraints on the box), rebuildable
    from the original; never in a log.
- Injuries (phase 6, ADR 012):
  - Only the answers are saved (questionnaire, check-ins, review day, closing
    day); the step of the path is replayed from them. The plan carries a
    snapshot without free text, so the doctor's notes and the pain scores never
    go into the saved plan.
  - A warning sign or a systemic illness leaves no session for the plan, with
    the reason and the invitation to see a doctor; the app gives no diagnosis
    and never decides the return (`ready_to_close` is information).
  - The ramp is +3 while a problem is open and for 28 days after the closing
    day, also when that day is dated ahead.
  - Schema v10 goes through the safety copy
    (`backup/ponos-v9-v10.sqlite3`) and is tested interrupted at every
    statement. Health data stay in the profile database in clear (the user's
    decision of 2026-11-05); nothing about them goes to a log, a message or an
    error chain (T-SEC-08).
- Garmin calendar (phase 7, ADR 013):
  - Responses of the calendar become domain types at the boundary; an entry
    with wrong types, no date or a name with control, invisible or
    direction-changing characters is left out, never repaired. A response that
    is not a calendar is a definitive error.
  - Every workout the app creates carries the prefix `Ponos · `. The library
    workout of an entry is deleted only if it has the prefix; the athlete's own
    workout is only taken off the calendar.
  - Deleting: read the definition, save the copy (with its SHA-256), check the
    entry is still the chosen one, unschedule, delete from the library. Nothing
    is deleted without its copy; a day before today is never touched; at most
    100 per deletion; if Garmin refuses, the copy is dropped so that "create
    again" never makes a duplicate. A copy whose hash does not match, or whose
    day has passed, is not created again.
  - Sending: the calendar is read first and a workout with the same name on the
    same day is not created again, also after a lost answer; at most 60 per
    send, counting only the sessions not yet on the calendar, so repeated sends
    advance; a 429 or a transient error is retried with the policy of the
    synchronisation and then stops.
  - The responses have the shape inferred from the library
    (`experiments/garmin_workouts.md`): no live test yet.
- Backup (phase 8, `docs/backup-format.md`):
  - One ZIP with a closed list of entries (database snapshot made with the
    SQLite backup API, original files, manifest with sizes and SHA-256). Never
    in it: tokens, lock, pre-migration copies, anything else.
  - Written in a private temporary folder next to the destination and renamed
    at the end with an exclusive rename: it never overwrites a file and never
    lands inside the profile folder; a dead process leaves only its temporary
    folder, which the next backup into the same folder removes (only a real
    folder with the generated name, whose process is gone or older than a day).
  - Restore: untrusted input. Central directory, number of entries, declared
    sizes, ratio and duplicates checked before extracting; names from a closed
    list with ASCII digits only; each file streamed, checked against its
    SHA-256, flushed and renamed; the database last, opened like a profile. It
    works in a temporary folder and gives it the name of the destination as its
    last step, so a failure or a death leaves the destination as it was; an
    empty folder the user created stays, and a folder with anything in it is
    refused.
  - The file is not encrypted (accepted risk, section 5).
- Release (phase 8): `release.yml` builds the Windows and Ubuntu packages,
  checks `SHA256SUMS`, attests the provenance of the archives and of a
  CycloneDX SBOM made from the lockfile, and attaches them to a draft release;
  publishing is manual. Only the attestation job has `id-token` and
  `attestations`. The executable is not signed (exception 2).

## 4. Did we do it right?

- Every threat in the table has at least one test, by id. The acceptance
  tester, who did not write the code, writes the in-depth tests.
- For every positive test there is a test with hostile input (T-SEC-01…13).
- T-SEC-13 (phase 8): re-reading of this document with a written judgement,
  path of the untrusted data followed by hand in the code, accepted risks
  reconfirmed.
- Every vulnerability found: root cause, similar cases, an automatic check that
  prevents its return.

### Threat → test matrix

| Threat | Test |
|---|---|
| Hostile FIT and fuzzing | T-SEC-01, T-SEC-02 |
| Hostile GPX and ZIP chosen by the user: never opened by the import | T-SEC-02, T-IMP-04 |
| Hostile ZIP downloaded from Garmin (bomb, entries, names, encrypted, nested) | T-SEC-02, T-SEC-03 (`tests/zipread`) |
| Times outside 1990–2099 | T-SEC-02 |
| Hostile JSON, ZIP and `Retry-After` from Garmin | T-SEC-03, T-SYNC-03 |
| Paths, links, reparse points, reserved names, scan of folders | T-SEC-04 |
| Deleting a profile folder | T-SEC-04, T-DATI-04, T-UI-03 |
| SQL | T-SEC-05 |
| Charts page | T-SEC-06, T-UI-04 |
| Secrets (password, MFA code, token; login, synchronisation, kill, logout) | T-SEC-07 |
| Log (health, paths, names, positions, controls, rotation, last-resort handler, whole synchronisation) | T-SEC-08 |
| Interrupted or racing profile creation | T-DATI-06 |
| Profile folder, permissions and archive | T-SEC-09 |
| Lock of the profile and of the list | T-DATI-04 |
| List of profiles and versioned formats | T-DATI-01 |
| Garmin calendar | T-SEC-10, T-UI-03 |
| Static analysis and dependencies | T-SEC-11 |
| Package | T-SEC-12 |
| Final review | T-SEC-13 |
| Hostile or interrupted synchronisation | T-SYNC-02, T-SYNC-03, T-SYNC-04, T-SYNC-06 |
| Full and incremental listing, slipping listing | T-SYNC-01, D-F2-04 |
| Matching with imports, download of another activity | T-SYNC-05, D-F2-03 |
| Token, login and MFA | T-SYNC-04, T-SEC-07 |
| Notifications | T-SEC-08 (`tests/platform/test_notifier.py`) |
| Import of FIT files and folders | T-IMP-01, T-IMP-02, T-IMP-03, T-IMP-04 |
| Migration that removes the GPX activities | T-DATI-01 (`tests/collaudo/test_fase8_solo_fit.py`) |
| Integrity and crash | T-DATI-01, T-DATI-02, T-DATI-03, T-DATI-05 |
| Resources | T-PERF-02, T-PERF-03 |
| Unexpected errors | T-UI-08, T-IMP-01 |
| Personal body data: limits, weight grammar, immutable fixed data | T-QUAL-02, T-DATI-01 |
| Interrupted or concurrent v3 migration | T-DATI-01 |
| Height, weight, date of birth and HR in logs | T-SEC-08 (`tests/collaudo/test_fase3_log_salute.py`) |
| Consistent load after changes of parameters and zones | T-CALC-01…07 |
| Phase 3 charts and windows (contrast, keyboard, accessible names, languages) | T-UI-04, T-UI-05, T-UI-06, T-UI-07 |
| Budget with 5,000 activities | T-PERF-01 |
| Nutrition: model, limits of the deficit, minors, low body mass index | T-NUT-01, T-NUT-02 |
| Nutrition: calories of the activities, declared and estimated | T-NUT-03 |
| Nutrition: meals and moments, daylight saving time | T-NUT-04 |
| Nutrition: determinism and dated changes | T-NUT-05 |
| (removed 2026-11-10) Table of foods: hostile file, examples, source and licence | T-NUT-06 |
| Migration v3 → v4 and the rules of the new tables | T-DATI-01 (`tests/collaudo/test_fase4b_migrazione.py`) |
| Nutrition data in logs and messages | T-SEC-08 (`tests/collaudo/test_fase4b_log_salute.py`) |
| Calendar and day page (accessibility, languages, memory of the settings) | T-UI-01, T-UI-06, T-UI-07 (`tests/collaudo/test_fase4b_interfaccia.py`) |
| Threshold test: window, HR and pace, refusals, hostile distance series | T-PIANO-06, T-SEC-01, T-SEC-02 |
| Threshold test: apply and undo, history, retest | T-PIANO-06 |
| Migration v4 → v5 and the rules of `threshold_test` | T-DATI-01 |
| Threshold HR and pace in logs and messages | T-SEC-08 |
| Saved plan and frozen days edited by hand; undo and interruptions of goals and plan | T-SEC-02, T-PIANO-04, T-DATI-02 |
| Nutrition of the days ahead, pause around the races | T-NUT-07, T-NUT-08 |
| Map channel (hostile messages, allowed calls) | T-SEC-14 |
| Map page without network; tile proxy, hostile answers, key | T-SEC-15, T-MAP-10 |
| Tile cache: cap, LRU, expiry, clear | T-MAP-11 |
| Map and list: speed with 50,000 activities, tracks, antimeridian | T-MAP-03, T-MAP-05, T-MAP-07 |
| Migration v7 → v8 (boxes of the tracks) | T-DATI-01 (`tests/collaudo/test_fase5b_migrazione_v8.py`) |
| Injuries: warning signs, path by steps, plan, ramp | T-INF-01, T-INF-02, T-INF-03 |
| Injuries in logs and messages | T-SEC-08 (`tests/collaudo/test_fase6_log_salute.py`) |
| Migration v9 → v10 (injuries) | T-DATI-01 (`tests/collaudo/test_fase6_migrazione_v10.py`) |
| Garmin calendar: send, delete, copies, create again, hostile responses | T-SEC-10, T-SEC-03, T-SYNC-03, T-SYNC-04 (`tests/collaudo/test_fase7_calendario.py`) |
| Backup and restore: round trip, closed list, hostile ZIP, interruptions | T-DATI-05, T-DATI-02, T-SEC-02 (`tests/collaudo/test_fase8_*.py`) |
| Token absent from the backup | T-SEC-07 |
| Package, `SHA256SUMS`, no local path, files read at run time | T-SEC-12 (`tests/collaudo/test_fase8_pacchetto.py`) |

### Cases closed by the phase 1 acceptance testing (D-F1)

Defects found by the phase 1 acceptance testing (D-F1-01…10, in the tests of
`tests/collaudo/`) and the behaviour that closes them. Every row has a test; a
defect is not considered closed without one. The defect tests have the id in
the name (`test_d_f1_NN_…`).

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F1-01 | A database from another program with `user_version` 1 produced a raw SQLite error instead of "damaged profile" | The tables of the schema are checked: it is a damaged profile and the file is not touched | T-DATI-01 | `tests/collaudo/test_database_del_profilo.py` |
| D-F1-02 | Orphan temporary files of an interrupted write stayed in the archive | At profile opening they are removed; in the log only the number | T-DATI-02 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-03 | Missing or altered archive file of an already present activity: no way to put it right | Result "repaired": by importing the original again the file is replaced atomically | T-DATI-03, T-IMP-03 | `tests/collaudo/test_integrita_e_cartella.py`, `tests/activities/test_import_use_case.py` |
| D-F1-04 | A file with a date in year 1 (then a GPX) made the load series cover centuries | Start time outside [1990-01-01, 2100-01-01) UTC: file rejected as damaged | T-SEC-02 | `tests/collaudo/test_date_ostili.py`, `tests/activities/test_instants.py` |
| D-F1-05 | The log received names of user folders and a name with a newline could fake a line | The formatter writes only exception types and neutralises control characters | T-SEC-08 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_diagnostics.py` |
| D-F1-06 | Permissions of the archive and of the original files not guaranteed to be private | They inherit the private permissions of the profile folder | T-SEC-09 | `tests/collaudo/test_sicurezza_input.py` |
| D-F1-07 | Whoever lost the race in creating a profile deleted the winner's database | The rollback undoes only what the creation did | T-DATI-06 | `tests/collaudo/test_due_istanze.py`, `tests/profiles/test_use_cases.py` |
| D-F1-08 | The open section was not remembered at restart | It is written at every change; an invalid value opens the Overview | T-UI-07 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-09 | A saved geometry of the wrong type blocked the opening of the window | Window of default size | T-UI-07 | `tests/collaudo/test_interfaccia.py`, `tests/ui/test_window_lifecycle.py` |
| D-F1-10 | The first drawing of the chart held the event loop for over 100 ms | Figure and JSON are built in the data thread; the web engine starts after the first `show()` | T-UI-02 | `tests/collaudo/test_prestazioni.py`, `tests/ui/test_window_lifecycle.py` |

### Cases closed by the phase 2 acceptance testing (D-F2)

Defects found by the review and the acceptance testing of the synchronisation
(D-F2-01…04, cited in the tests) and the behaviour that closes them. The defect
tests have the id in the name (`test_d_f2_NN_…`) or in a comment.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F2-01 | `Retry-After` with a digit that `float()` does not read (for example an apostrophe) made the adapter raise | Only ASCII digits are accepted, integer or decimal; otherwise no suggested wait and only the backoff applies | T-SYNC-03 | `tests/collaudo/test_fase2_garmin_ostile.py`, `tests/garmin/test_errors.py` |
| D-F2-02 | A suggested wait that was negative, NaN or infinite made the use case raise instead of giving a result | `RateLimitedError` carries an implausible value; the synchronisation stops with "rate limit", without sleeping | T-SYNC-03 | `tests/collaudo/test_fase2_garmin_ostile.py` |
| D-F2-03 | A download that was another activity than the listed id was recorded under that id | The file must match the entry (start and duration, duplicate rule); otherwise the entry fails definitively (`REMOTE_REJECTED`) and nothing is recorded | T-SYNC-05 | `tests/collaudo/test_fase2_sincronizzazione.py`, `tests/activities/test_sync_errors.py` |
| D-F2-04 | If Garmin received a new activity during the first synchronisation, the oldest slipped beyond the declared total and was not listed | The listing ends with a short or empty page, never at the declared total; the total serves only the progress; the full check recovers what had escaped | T-SYNC-01 | `tests/collaudo/test_fase2_sincronizzazione.py`, `tests/activities/test_sync_volume.py`, `tests/activities/test_sync_domain.py` |

### Cases closed by the phase 3 acceptance testing (D-F3)

Defects found by the phase 3 acceptance testing (D-F3-01…11) and the behaviour
that closes them. All closed on 2026-11-06: their tests, born as expected-fail
(strict `xfail`), pass without the mark.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F3-01 | `Weight.parse` accepted "7_0.5", non-ASCII or full-width digits and exponents | Only ASCII digits with one decimal separator | T-QUAL-02 | `tests/collaudo/test_fase3_dati_personali.py`, `tests/profiles/test_body_types.py` |
| D-F3-02 | `SportWeightsForm.set_value(nan)` made `build()` raise | A NaN or an infinity does not change the value in force; `build()` always gives weights between 0 and 5 (form removed on 2026-11-10 with the sport weights) | T-QUAL-02 | `tests/collaudo/test_fase3_dati_personali.py` |
| D-F3-03 | The three HR boxes of "Edit profile" had no accessible name (the label was bound to the container, not to the box) | Each box has its own accessible name | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-04 | In "New profile" name, folder and HR boxes without an accessible name (same cause as D-F3-03) | Each control has its own accessible name | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-05 | In "Edit profile", for a profile without fixed data, Tab started from the measurements and visited height and date of birth, at the top of the dialog, last | The Tab order follows the visual order, from the top | T-UI-05 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-06 | Zone names in the HR chart below 4.5:1 on the zone bands | Text contrast at least 4.5:1 on every band, in both themes | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-07 | The daily load bars (opacity 0.6) rendered below 3:1 on the background | Bars at least 3:1 on the background, as drawn | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-08 | The crosses of the discarded samples below 3:1 on the zone bands of the dark theme | Crosses at least 3:1 on every band | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-09 | CTL and ATL differed only by colour (same line, same thickness) | The lines can be told apart even without colour | T-UI-05 | `tests/collaudo/test_fase3_grafici.py` |
| D-F3-10 | In English the plurals stayed "activity(ies)" and "sample(s)": the plural form does not exist for the source language | English plurals have the true form for the number, without an optional ending | T-UI-06 | `tests/collaudo/test_fase3_interfaccia.py` |
| D-F3-11 | In the "Activity detail" window the labels of the message page (loading, missing file, altered file) stayed lower than the text: truncated lines | The labels show every line of their text | T-UI-06 | `tests/collaudo/test_fase3_interfaccia.py` |

Cases closed by the review and the fix of the core (the `R` ids are those cited
in the tests):

| Id | Case | Behaviour | Test (plan) |
|---|---|---|---|
| R1 | Unexpected error stops the import | Failed result for that file; the import continues | T-IMP-01 |
| R4 | Close that waits for a job in progress | The window stays open until the data thread finishes, without blocking the UI; over the cap it warns | T-UI-02, T-UI-08 |
| R5 | No way to see the logs | Help menu, log folder; log on file with rotation | T-SEC-08 |
| R11 | Start times without a plausibility window | See D-F1-04 | T-SEC-02 |
| R14 | List of profiles unusable | Message with the location of the file; the window stays usable | T-DATI-01 |
| — | Deletion too wide | Only the names the app puts in the folder; refused if there is anything else, a link or an open profile | T-SEC-04, T-DATI-04 |
| — | Migration without a safety net | Copy of the database in `backup/ponos-v{from}-v{to}.sqlite3` before every migration; without a copy it does not migrate | T-DATI-01 |
| — | Preview that modifies the profile | `peek` opens the database read-only, without migrating or taking the lock | T-UI-03 |
| — | File replacements with different techniques | One single module, `ponos_coach.fsio` | T-DATI-02 |

### Cases closed by the phase 5 acceptance testing (D-F5)

Defects found by the phase 5 acceptance testing and the behaviour that closes
them. Closed on 2026-11-08.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F5-01 | A saved plan edited by hand with its checksum recomputed (minutes 0 or negative, a negative load, an expected time of 0) was read back and broke the nutrition of that day | The values of a session, a race and a day are valid by construction: a plan with impossible numbers cannot be built, so the decoder reads it as unreadable and the next rebuild writes a good one | T-SEC-02 | `tests/collaudo/test_fase5_piano_manomesso.py`, `tests/planning/test_plan_codec.py` |
| D-F5-02 | Today, inside the week of an A or B race or the day before a taper, still had the weight-loss deficit while tomorrow had none | Today has no deficit in the week (Monday to Sunday) of an A or B race of the saved plan, or when the first day of the plan is in a taper or mini-taper | T-NUT-08 | `tests/collaudo/test_fase5_alimentazione_futura.py` |
| D-F5-03 | With a gap of 90 minutes between two sessions the second refuelling fell at the minute the next session starts (the 30 minutes that the first intake waits were not subtracted) | The refuelling hours count the wait after the end of the session and the 30 minutes before the next one; the first intake is always there | T-NUT-04, T-NUT-07 | `tests/collaudo/test_fase5_rifornimento_orario.py` |
| D-F5-04 | Late-evening refuelling intakes after midnight were all clamped to 23:59 and piled on the same minute | Intakes that would fall after the end of the day are left out; the first one stays, at 23:59 at the latest | T-NUT-04 | `tests/collaudo/test_fase5_rifornimento_orario.py` |

Closed on 2026-11-08: their tests, born as expected-fail (strict `xfail`), pass
without the mark.

### Cases closed by the phase 5b acceptance testing (D-F5B)

Closed on 2026-11-08.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F5B-01 | The pattern for a tile address and for the size of the cache used `\d`, which matches Unicode digits: Arabic-Indic digits were read as 12/0/0 and as a size | Only ASCII digits are accepted, in the address and in the size | T-SEC-15 | `tests/collaudo/test_fase5b_tile.py` |
| D-F5B-02 | The cool-down was global: one 404 (a tile the provider does not have) silenced the tiles around it for 15 seconds | A bad answer for one tile cools down that tile only (a table of at most 512 tiles); the network problems and the key stay global | T-MAP-10 | `tests/collaudo/test_fase5b_tile.py`, `tests/maps/test_use_cases.py` |
| D-F5B-03 | The write-ahead log of the tile cache grew past the cap of the folder | The WAL is truncated after every write: the folder stays within the cap plus a small margin | T-MAP-11 | `tests/collaudo/test_fase5b_tile.py` |

### Cases closed by the phase 6 acceptance testing (D-F6)

Closed on 2026-11-08.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F6-01 | An injury closed on a day dated ahead was neither open nor "recently returned": for those days the plan went back to a ramp of +5 | A closing dated ahead keeps the ramp at +3 until 28 days after the closing day | T-INF-03 | `tests/collaudo/test_fase6_piano.py`, `tests/injury/test_restriction.py` |

### Cases closed by the phase 7 acceptance testing (D-F7)

Closed on 2026-11-08.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F7-01 | The copy of a workout was saved before the removal was tried; when Garmin refused (entry changed), the copy stayed pending and "create again" made a workout that was never deleted | If the removal does not happen the copy is dropped again; nothing is pending | T-SEC-10 | `tests/collaudo/test_fase7_calendario.py` |
| D-F7-02 | `deferred` promised "left for another send", but the next send chose the same first 60 sessions (already on the calendar) and deferred the same rest: a long plan went one week at a time | The cap of 60 counts only the sessions not yet on the calendar, so repeated sends advance until the plan is complete | T-SYNC-03 | `tests/collaudo/test_fase7_calendario.py` |
| D-F7-03 | The filter on the names of the calendar entries dropped control characters but not the format characters that reorder or hide text, in the dialog where the user chooses what to delete | An entry whose name carries invisible or direction-changing characters is left out of the list | T-SEC-03, T-SEC-10 | `tests/collaudo/test_fase7_calendario.py` |

### Cases closed by the phase 8 acceptance testing (D-F8)

Closed on 2026-11-08.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F8-01 | After a power-off during a restore the target held some archive files and no database; the restore refuses a non-empty folder, so the user could not retry | The restore works in a temporary folder next to the destination and gives it the destination's name as its last step: the destination stays as it was | T-DATI-05, T-DATI-02 | `tests/collaudo/test_fase8_interruzioni.py` |
| D-F8-02 | A backup process that died left its `.ponos-backup-*` temporary ZIP (a partial copy of the user's data) in the chosen folder for ever | The next backup into the same folder removes it, only if it is a real folder with the generated name whose process is gone or that is more than a day old | T-DATI-02, T-SEC-09 | `tests/collaudo/test_fase8_interruzioni.py` |
| D-F8-03 | `docs/backup-format.md` said a failed restore removes the folder only "if it created it", but any empty folder was removed, also one the user had made | Only a folder the restore created is removed; an empty folder of the user stays | T-DATI-05 | `tests/collaudo/test_fase8_backup.py` |
| D-F8-04 | The pattern of the entry names used `\d` (any Unicode digit): a backup could carry a year folder in Arabic-Indic digits and the restore created it; the writer (`str.isdecimal`) was as lenient | The year is 4 ASCII digits, in the reader and in the writer | T-SEC-02 | `tests/collaudo/test_fase8_zip_ostili.py` |

### The map page (phase 5b, T-SEC-14 and T-SEC-15)

The activity-detail window has its own small map page, `ponos://app/track.html` (`track.js`,
Leaflet from `web/vendor/`), listed in `web_resources.py` and with the same CSP as the map
(`connect-src 'none'`, `img-src` only `data:`, `blob:` and `ponos://tiles`). It differs in one
way that narrows the surface: it has no channel at all (no `qwebchannel.js`, no bridge object),
so the page cannot tell Python anything. Python sends one JSON call (`ponosTrack.show`) with the
thinned positions of that activity, the colours of the theme and the texts; the tiles come from the
same proxy (`ponos://tiles/z/x/y`), so the page never talks to the tile server. Without network or
when the server refuses, the page keeps a plain background with the track and the
window shows one line with the reason. Both maps show "© OpenStreetMap contributors". Tests: `tests/ui/test_activity_track.py` (the page asks
only through the app scheme, cannot reach other hosts, holds no key, is freed when the window
closes) and `tests/ui/test_vendor_libraries.py` (closed list, local sources only, no network call
in the script).

Accepted risk: both `map.html` and `track.html` carry `style-src 'unsafe-inline'`, because Leaflet
sets inline styles (position and size of tiles, markers and panes) and a strict style policy
would break it. The risk is that injected markup could restyle the page; it is accepted because
no HTML is composed from data (texts reach the page as text, never as markup: no `innerHTML`,
tested), `script-src` stays strict, and `connect-src 'none'` means styling cannot be used to
send anything out. Scripts are never allowed inline.

- **Page**: `ponos://app/map.html`, same engine and interceptor as the charts;
  CSP `default-src 'none'`, `connect-src 'none'`, `img-src` only `data:`, `blob:`
  and `ponos://tiles`. Scripts: Leaflet and markercluster from `web/vendor/`
  (hash-checked by test), `qwebchannel.js` from Qt, `map.js` (no `fetch`, no
  `eval`, no `innerHTML`: tested).
- **Tiles**: `ponos://tiles/{source}/z/x/y` only (interceptor: a source of
  the closed set, integers inside the pyramid of that source, no query,
  fragment or port); the scheme handler asks `TileProxy` in
  a worker pool (at most 256 waiting) and never the network from the page.
- **Channel**: one `QWebChannel` object, slots taking numbers and one text
  (`pageReady`, `viewChanged`, `activityClicked`, `activityOpened`,
  `pointHovered`, `pointLeft`, and since 2026-11-10 `backgroundChosen(key)`,
  which accepts only a key of the closed set of backgrounds and counts the
  rest as rejected). The page names an activity only by its position in the list
  Python sent; every value is validated (`map_messages`) and rejected ones
  are counted, with no effect. JSON cannot carry NaN or infinity: the channel
  turns them into 0, a valid and harmless value. Qt also publishes
  `QObject.deleteLater` on the object: a compromised page could delete the
  bridge and so silence its own map (accepted, no other effect).
- **Positions**: never in logs; the map's last view is not saved (a position in
  `QSettings` would be a file outside the planned folders). The tile cache keeps
  tile addresses (the areas looked at) in the app's cache folder, not in the
  profile.
- **No key** (since 2026-11-09): the map has no secret, so the settings page, the
  key store and the checks that searched for a placeholder key are gone. What the
  adapter sends is the tile path, the `User-Agent` and the stored validators.

### Cases closed by the phase 4b acceptance testing (D-F4B)

Defects found by the phase 4b acceptance testing (D-F4B-01…09) and the
behaviour that closes them. The defect tests have the id in the name
(`test_d_f4b_NN_…`) or in a comment; D-F4B-06 and D-F4B-08 concerned the food table, removed on 2026-11-10 with their tests.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F4B-01 | A woman of 98 years, 100 cm and 20.1 kg (inside the limits of the person) with a 100 g loss goal made the day fail with "macronutrient grams cannot be negative" | Carbohydrate room and protein cap of the day's macros are clamped at zero, so no quantity is negative; every adult inside the limits gets a day, under 18 the day is unavailable by design | T-NUT-02, T-NUT-04 | `tests/collaudo/test_fase4b_modello.py` |
| D-F4B-02 | Declared calories of 50,000 kcal in 20 minutes were taken at face value and inflated the target | A total above 1,500 kcal per hour of moving time is discarded and estimated as if absent | T-NUT-03 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-03 | In an eight-hour ultra the "during" box said 60 g of carbohydrate an hour, but the item held far fewer grams, and dinner kept none | Box and grams agree; lunch and dinner keep at least 1 g/kg of carbohydrate | T-NUT-04 | `tests/collaudo/test_fase4b_modello.py` |
| D-F4B-04 | The recovery after a session with long stops was placed at the end of the moving time, while the person was still out | The end of a session is the start plus the elapsed time | T-NUT-04 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-05 | A date of birth of 1900 and a day in 2035 (135 years) raised an exception | The result is "unavailable" (`AGE_OUT_OF_RANGE`) | T-NUT-01 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-06 | A food table nested 100,000 levels deep exhausted the JSON decoder's recursion | The depth (at most 8) is checked before the decoder; the error is a `FoodTableError` | T-NUT-06 | (removed 2026-11-10) |
| D-F4B-07 | A session across the change to summer time had its recovery before its end on the wall clock | The end is computed in UTC and then shown in local time | T-NUT-04 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-08 | The source and licence of the food table (USDA FoodData Central, CC0) were not in the dependency register | Entry "USDA FoodData Central" in `docs/dipendenze.md` | T-NUT-06 | (removed 2026-11-10) |
| D-F4B-09 | An activity indexed before v4 lost its declared calories and was estimated | The day reads the original file and uses the declared calories | T-NUT-03 | `tests/collaudo/test_fase4b_giorni.py` |

All closed on 2026-11-07: their tests, born as expected-fail (strict `xfail`),
pass without the mark.

### Cases closed by the phase 4 acceptance testing (D-F4)

Defects found by the phase 4 acceptance testing and the behaviour that closes
them.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F4-01 | A 30:00 test recorded at 1 Hz has its last record at 29:59 and lost its pace | The distance counts until one sampling interval (at most 1 s) after the last record, as the HR does | T-PIANO-06 | `tests/collaudo/test_fase4_soglia_e2e.py` |
| D-F4-02 | The start of the segment was counted from the start of the session, not from the first record, and was early when the first record came later | The segment starts at the instant of its record; HR and distance share the first record as origin | T-PIANO-06 | `tests/collaudo/test_fase4_soglia_e2e.py` |
| D-F4-03 | The profile session did not expose the threshold test history | `ProfileSession.threshold_tests` | T-PIANO-06 | `tests/collaudo/test_fase4_soglia_e2e.py` |
| D-F4-04 | A distance jump (20 km in 1 s) outside the test attracted the "greatest distance" window and moved the threshold HR | Steps above 12 m/s are ignored when choosing the window; a window with an implausible step or pace falls back to the choice by heart rate, without pace | T-PIANO-06 | `tests/collaudo/test_fase4_soglia_e2e.py` |
| D-F4-05 | A history row with an impossible start instant raised `OverflowError` instead of a store error | Any conversion error of the row becomes `AssessmentStoreError` | T-DATI-01 | `tests/collaudo/test_fase4_migrazione.py` |
| D-F4-06 | A record earlier than the first one put HR and distance samples before second zero | Samples before second zero are dropped from both series; load and cache fingerprint unchanged | T-SEC-02 | `tests/collaudo/test_fase4_fuzz.py` |

All closed on 2026-11-07: their tests, born as expected-fail (strict `xfail`),
pass without the mark. The review also made the choice by heart rate consider
only windows with valid HR on at least 90% of the last 20 minutes.

## 5. Accepted risks

| Risk | Reason |
|---|---|
| Unofficial Garmin API: it can break, can violate the terms of use, can lead to the blocking of the account | There is no official access for private users (ADR 002). Mitigations: isolated adapter, few requests, FIT import always available. |
| Unsigned executables (exception 2 of `CLAUDE.md`, permanent) | Personal project without a signing certificate. Mitigation: `SHA256SUMS`, provenance of the archives and an SBOM attested by the release workflow, README with hash verification and SmartScreen notice. |
| Tokens readable by every process of the user | DPAPI in user scope and the Secret Service protect from other users, not from other processes of the same user. Credential Manager, rejected because it cannot hold the tokens (2560 bytes), would have had the same limit. A password the user must type at every start would go against daily use. The machine is assumed not compromised. Mitigation: logout deletes the tokens. |
| Non-interruptible library timeouts | The timeout belongs to the library (15 s per request, 30 s per login step) and cannot be configured without touching internal parts. Cancellation applies from the next request: a request already sent ends or times out on its own. Mitigation: the journal makes resuming safe at every point. |
| Response or download read entirely into memory by the library before the cap | The cap of 50 MB applies after the read. Mitigation: the 15 s timeout and the ZIP limits downstream (T-SEC-02). |
| DPAPI entropy is not secret | The code is public; the entropy ties the file to the app and the profile, it does not protect from the user themselves. |
| Wrong matching between two almost equal activities (60 s, 5%, same sport) | The imported original file stays; the other activity is not downloaded. Better not to lose data. |
| Health data in plain text in the profile folder, protected only by the system permissions | Simplicity and opening with other tools; encrypting requires managing a password and a recovery key. Accepted by the user on 2026-11-05. From phase 3 it also holds height, weight (with the history) and date of birth. Mitigation: folder permissions for the user only (T-SEC-09); the README recommends disk encryption (BitLocker on Windows, LUKS on Ubuntu). |
| Copies of the database in `backup/` and archive files with the same health data | Same risk and same mitigation as the profile folder: same private permissions. |
| Profile folder on a network or in a synchronised folder | The app warns but does not forbid: the user may have a reliable NAS. On these places the SQLite lock and WAL are not guaranteed; a damaged database is reported at opening. |
| Incomplete detection of network and cloud | UNC, remote drives, network mounts, OneDrive, Dropbox, Google Drive and iCloud Drive are recognised by name and variables; an unknown service is not recognised. When in doubt the type is unknown and there is no warning. |
| List of profiles readable by every process of the user | It contains paths and names of profiles, not health data; it is in the user's configuration folder. |
| An attack with the user's permissions can replace the archive and its database together | Integrity protects from errors and partial modifications, not from whoever already controls the account (assumptions). |
| A GPX archive file left behind after the v11 migration (interruption between the commit of the database and the removal of the files) | It is an orphan file, harmless and not cited by the database; the opposite (a record without its file) cannot happen because the database commits first. |
| Activities imported from GPX removed from the profile without the user confirming each one | Decision of the user (2026-11-09, ADR 014): copy of the database in `backup/ponos-v10-v11.sqlite3` before the migration and a notice with the number of activities removed when the profile opens. |
| No secure deletion of data | Beyond the reach of an app on ordinary file systems. |
| Guidance built on population formulas (Mifflin-St Jeor, Keytel, Boer, factors without sport) and on self-assessed level | Individual error of hundreds of kcal is possible, and an app cannot measure more. Mitigations: conservative limits of the deficit, no numbers under 18 years, fixed notice, no diagnosis (ADR 008). |
| Declared calories that are wrong but plausible | Under 1,500 kcal per hour they cannot be told from a real effort. Only the net energy (without the rest rate) is added to the expenditure, which reduces the effect. |
| A threshold that is wrong but plausible (a field test, a hard day, a fast downhill window) | It cannot be told from a real effort within the limits of the refusals. Mitigations: the user chooses the activity, the refusals with reasons, the exact undo, the retest every 6–8 weeks; the zones are a training indication, not medical advice (ADR 009). |
| Distance from a GPS or a treadmill sensor with a systematic error | The pace is only as good as the distance; the app cannot measure more. The HR threshold does not depend on it. |
| Nutrition advice is general; no allergy or diet restriction | User decision of 2026-11-07. Since 2026-11-10 there are no example foods: the meals show energy and macronutrients only. |
| The tile server in use (`tile.openstreetmap.org`, `*.tile.opentopomap.org` or `*.tile-cyclosm.openstreetmap.fr`) sees the areas the user looks at, and its address (and so the IP of the user); only the server of the background in use sees them | Accepted by the user on 2026-11-07 (ADR 010); the host changed on 2026-11-09 and the choice of three on 2026-11-10; the risk is the same, with more hosts but one at a time. A map with tiles needs a tile host; the alternative, an offline vector region, is deferred. Mitigations: the page has no network, one host over HTTPS only, the URL is never logged, only tiles that were looked at are asked (no prefetch), the cache follows the headers and is checked with conditional requests, a neutral background when the server cannot be reached. |
| OpenStreetMap, OpenTopoMap (sometimes slow) or CyclOSM (OpenStreetMap France servers, paid for with donations) can block the app at any time, with no notice and no guarantee of service; the URLs are fixed in the code | Accepted by the user on 2026-11-09 (ADR 010 amendment). The usage policy is followed (see the section on the map), a refusal pauses all requests for 300 s, and the map is a convenience: list, filters and tracks keep working. If the block lasts, a change of source is a code change in one module. |
| The areas looked at stay in the tile cache (a database in the user's cache folder) | Same readers as the profile (every process of the user). It is outside the profile folder and its backups, derived, and "Clear map cache" empties it. |
| A compromised map page can delete its own bridge (`deleteLater`) | The only effect is that its own map goes quiet. No other call is open. |
| Shape of the Garmin workout and calendar responses not verified on the live service | The adapter is written from the library's code; only `garmin/workout_json.py` changes if Garmin differs. Mitigations: hostile or odd entries are left out, deletion needs a copy and a still-matching entry, `T-MAN-01` is the check by the user. |
| The backup file is not encrypted and holds health data and GPS positions | Declared in the format, in the interface and in the README. Encrypting needs a password and a recovery key, as for the profile (accepted on 2026-11-05: same decision). Mitigations: private permissions where the file system has them, never in the profile folder, no tokens in it, the advice to use disk encryption and not to keep it in a synchronised folder. |
| A backup made by the user is then outside the protection of the profile folder | The app cannot follow a file the user moves. The warning is in the interface. |
| Injuries and illnesses in plain text in the profile database | Same decision and mitigations as the other health data (accepted on 2026-11-05); the saved plan holds no free text. |
| A frozen day of the plan cannot be removed except by deleting the profile | Decision of phase 5: the history of what was planned must not be rewritten. |

## Open points

- The final review of this document (T-SEC-13) is part of the release
  checklist and is not done yet.
- No test covers the cap of 1,000,000 listed entries
  (`MAX_LISTED_ACTIVITIES`).
- The real shape of the Garmin responses (sport keys, listing fields) is
  verified only on the fixtures: the live spike of phase 0 is missing, which
  the user launches (`experiments/README.md`). The `SyncPolicy` values must be
  reviewed with its result.
- The behaviour of closing during a login or a download is evolving in phase
  2; to re-read with the final version.
- The id D-F3-01 does not appear in any test of phase 3: to assign to a defect
  or to leave empty.
- The D-F1 and D-F2 defects are not yet recorded in `docs/collaudo.md`: the
  main session does it.
- The rows of D-F5-02 and D-F5-04 are missing from the table of phase 5.
- The Garmin workout and calendar responses are checked only on fixtures:
  `T-MAN-01`. The OpenStreetMap tile server is checked only on a local server
  that plays hostile answers; the reading of its usage policy before the
  release stays open.
