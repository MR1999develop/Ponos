# Threat model

Phase 4 version (2026-11-07): adds the threshold test (the distance series
read from FIT and GPX files, the history of the tests in schema v5, threshold
HR and pace, pace zones). It builds on that of phase 4b (2026-11-07: the
nutrition of the day: activity calories, activity level, meal times, goal
periods, the table of foods and the guidance itself), on that of phase 3 (2026-11-06: height, weight and
date of birth as personal health data, dated measurements, sport weights, HR
zones and activity details), on that of phase 2 (2026-11-06: login in the app, token
store, Garmin responses and downloads, library log and notifications), on that
of phase 1 (2026-11-05, updated on 2026-11-06 with the acceptance defects and
the log boundary) and on phase 0. To be updated again in the same change as
every function that touches a trust boundary. The test identifiers
(`T-SEC-04`…) are those of section 7 of `PLAN.md`.

## 1. What are we building

Local desktop app (Windows and Ubuntu) for one user at a time per profile. It
downloads activities from Garmin Connect with an unofficial login, reads FIT
and GPX files (also inside ZIPs), computes the load, shows charts in a local
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
   | garmin (adapter)      |      | Import of files, folders and ZIPs    |
   +-----------+-----------+      |  (B2) FIT, GPX, ZIP: untrusted       |
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
 | FIT/GPX  |  | notif.,   |  | (B6)      |  | QWebEngineView  |
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
| B2 | FIT, GPX and ZIP files imported by hand, dragged or from a folder → app | files, names, paths, links, archive entries |
| B3 | App ↔ profile folder | database, archive, backup, lock, permissions |
| B4 | App → charts web page | chart data as JSON, activity names |
| B5 | App ↔ token store (Windows: file encrypted with DPAPI in `secrets/` in the app's local data folder; Ubuntu: Secret Service) | Garmin tokens, by profile id |
| B6 | App → log file in the user's log folder (`ponos-coach.log`) | events, exception types |
| B7 | Development → user | executable, dependencies, release workflows |
| B8 | App ↔ configuration folder | list of profiles (id, folder, name, last use), lock of the list |
| B9 | User → login dialog → `garminconnect` library | email, password, MFA code (in memory only, in the login thread) |
| B10 | App → system notifications | fixed title and number of new activities |
| B11 | Table of foods (`foods.json`, shipped with the app) → parser | values per 100 g, ids, names, portions. It is part of the package, but it is treated as input: a mistake or a change of source must not reach the guidance |
| B12 | FIT `session.total_calories` (inside B1 and B2) → model | calories declared by the device: untrusted until checked against the duration |
| B13 | FIT `record.distance` and `record.speed`, GPX track points (inside B1 and B2) → distance series → threshold test | cumulative distance and its time axis: untrusted until they form a valid series (finite, increasing time, non-decreasing metres) |

### Assets

| Asset | Why it matters |
|---|---|
| Garmin credentials and tokens | access to the user's account; the password and the MFA code live only in the login thread, the tokens in their store |
| The user's Garmin account | block (429) or flagging for unusual use; modifiable calendar |
| Health data: HR, injuries, pain, doctor's notes | confidentiality |
| Height, weight and date of birth (with the weight history) | personal health data: they are only in the profile database; never in logs, messages or error chains; in plain text like the other profile data (accepted risk, section 5) |
| Threshold HR and threshold pace, with the history of the tests | health data: only in the profile database; never in logs, messages or error chains. They move the zones and the load of every later activity, so their integrity matters too |
| GPS positions of the activities | they reveal home and habits |
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
| B2 | FIT or GPX with absurd start or end (outside 1990–2099 UTC) that pollute archive, table and calculations | T | T-SEC-02, T-GPX-01 |
| B2 | GPX with DTD or entities (even external), exponential expansion (XXE, billion laughs) | I, D | T-SEC-02, T-GPX-01 |
| B2 | GPX with deep nesting, millions of points or huge texts | D | T-SEC-02, T-GPX-01 |
| B2 | GPX with `.gpx` extension and content of another type (FIT, TCX) or the reverse | T | T-SEC-02, T-GPX-01 |
| B2 | ZIP: decompression bomb, ratio over 100:1, too many entries, false declared sizes | D | T-SEC-02, T-IMP-04 |
| B2 | ZIP: names with `../`, absolute paths or reserved names written outside the archive | T, E | T-SEC-02, T-SEC-04, T-IMP-04 |
| B2 | Nested ZIPs, encrypted entries, wrong CRC | D, T | T-SEC-02, T-IMP-04 |
| B2 | Symlinks, junctions, reparse points, Windows reserved names, long paths in the scan of a folder | T, I, E | T-SEC-04 |
| B2 | File replaced by a link between the check and the read | T, I | T-SEC-04 |
| B2 | Huge folder (whole disk) | D | T-IMP-02, T-PERF-02 |
| B2 | Duplicates or altered files that change the past; same activity in FIT and GPX | T | T-IMP-01, T-IMP-03, T-GPX-02, T-SYNC-05 |
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
| B3 | Load of a previous version mixed with the new ones after a change of parameters, weights, zones or model | T | T-CALC-05, T-CALC-01 |
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
| B3 | Weights changed during a synchronisation: loads that do not match the calculation from scratch | T | T-CALC-05, `tests/collaudo/test_fase3_scenari.py` |
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
| B11 | Food table too large, too deeply nested, with duplicate ids, out-of-range or non-finite values, energy not matching the macros, too few or too many entries | T, D | T-NUT-06, D-F4B-06 |
| B11 | Food source and licence not recorded; values never checked against the source | T | T-NUT-06, D-F4B-08; accepted risk (section 5) |
| B12 | Declared calories absurd for the duration (50,000 kcal in 20 minutes) that inflate the expenditure and the target | T | T-NUT-03, D-F4B-02 |
| B12 | Zero or invalid FIT calories read as real | T | T-NUT-03 |
| B3 | Migration v3 → v4 interrupted midway, done by two instances, or without a copy | T, D | T-DATI-01 |
| B3 | Overlapping goal periods, also by an `UPDATE` of the dates; level, meal times or pace out of range | T | T-DATI-01, T-UI-01 |
| B3 | Weight, age, HR, level, goals and meal times in logs, messages or error chains | I | T-SEC-08 (`tests/collaudo/test_fase4b_log_salute.py`) |
| B3 | Activity indexed before v4 without calories that makes the day wrong | T | T-NUT-03, D-F4B-09 |
| B13 | Distance that is NaN or infinite (FIT float field, speed, GPX coordinates) that breaks the window search or gives a nonsense pace | T, D | T-PIANO-06, D-F4 |
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
| Calendar | Deletion of unwanted workouts on Garmin (phase 7) | T | T-SEC-10, T-UI-03 |
| Plan | Dangerous plan from wrong data or wrong rules | T | T-PIANO-02, T-PIANO-03, T-PIANO-04, T-INF-01, T-INF-02, T-INF-03 |
| UI | Interface blocked by a long operation | D | T-UI-02, T-SYNC-06 |

## 3. What we do

- External inputs (JSON, FIT, GPX, ZIP, file names) become domain types at the
  boundary, with limits checked before reading or allocating; order: origin,
  size, characters, syntax, semantics. We reject, we do not repair. One single
  point decodes FIT and GPX, with the same error.
- The format is recognised from the content and must match the extension. A
  start or an end (start plus elapsed time) outside [1990-01-01, 2100-01-01)
  UTC makes the file damaged.
- GPX: XML read by events with `defusedxml`, without DTD, entities or external
  resources; limits of 50 MB, depth 32, 1,000,000 points and 4096 characters
  per text, checked before and during reading.
- ZIP: central directory read with a cap, number of entries, sum of sizes and
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
- The names of the archive files (`archivio/<year>/<sha256>.<fit|gpx>`) are
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
  encoded JSON, no `QWebChannel`.
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
- Measurements, sport weights and fixed data are written in a single
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
  - Food table (B11): size cap of 256 KiB and nesting depth of at most 8,
    both checked before the JSON decoder; then ids, ranges, finite values,
    energy consistent with the macros (±15% or 15 kcal), between 40 and 200
    entries; anything else is a `FoodTableError`, never a repaired value. The
    file is read once by the Composition Root; the domain gets a `FoodTable`.
    Source and licence are in the file and in `docs/dipendenze.md`;
    `experiments/verify_foods.py` checks the values before the release.
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
    without inventing distance over gaps above 10 s; the GPX distance counts no
    step between two segments. `DistanceSeries` checks its own invariants
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
| Hostile GPX (XXE, expansion, depth, points) | T-SEC-02, T-GPX-01 |
| Hostile ZIP (bomb, entries, names, encrypted, nested) | T-SEC-02, T-IMP-04 |
| Times outside 1990–2099 | T-SEC-02, T-GPX-01 |
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
| Import of files, folders and ZIPs | T-IMP-01, T-IMP-02, T-IMP-03, T-IMP-04 |
| Duplicates between FIT and GPX | T-GPX-02 |
| Integrity and crash | T-DATI-01, T-DATI-02, T-DATI-03, T-DATI-05 |
| Resources | T-PERF-02, T-PERF-03 |
| Unexpected errors | T-UI-08, T-IMP-01 |
| Personal body data: limits, weight grammar, immutable fixed data | T-QUAL-02, T-DATI-01 |
| Interrupted or concurrent v3 migration | T-DATI-01 |
| Height, weight, date of birth and HR in logs | T-SEC-08 (`tests/collaudo/test_fase3_log_salute.py`) |
| Consistent load after changes of parameters, weights and zones | T-CALC-01…07 |
| Phase 3 charts and windows (contrast, keyboard, accessible names, languages) | T-UI-04, T-UI-05, T-UI-06, T-UI-07 |
| Budget with 5,000 activities | T-PERF-01 |
| Nutrition: model, limits of the deficit, minors, low body mass index | T-NUT-01, T-NUT-02 |
| Nutrition: calories of the activities, declared and estimated | T-NUT-03 |
| Nutrition: meals and moments, daylight saving time | T-NUT-04 |
| Nutrition: determinism and dated changes | T-NUT-05 |
| Table of foods: hostile file, examples, source and licence | T-NUT-06 |
| Migration v3 → v4 and the rules of the new tables | T-DATI-01 (`tests/collaudo/test_fase4b_migrazione.py`) |
| Nutrition data in logs and messages | T-SEC-08 (`tests/collaudo/test_fase4b_log_salute.py`) |
| Calendar and day page (accessibility, languages, memory of the settings) | T-UI-01, T-UI-06, T-UI-07 (`tests/collaudo/test_fase4b_interfaccia.py`) |
| Threshold test: window, HR and pace, refusals, hostile distance series | T-PIANO-06, T-SEC-01, T-SEC-02 |
| Threshold test: apply and undo, history, retest | T-PIANO-06 |
| Migration v4 → v5 and the rules of `threshold_test` | T-DATI-01 |
| Threshold HR and pace in logs and messages | T-SEC-08 |

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
| D-F1-04 | A GPX with a date in year 1 made the load series cover centuries | Start time outside [1990-01-01, 2100-01-01) UTC: file rejected as damaged | T-SEC-02, T-GPX-01 | `tests/collaudo/test_date_ostili.py`, `tests/activities/test_instants.py` |
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
| D-F3-02 | `SportWeightsForm.set_value(nan)` made `build()` raise | A NaN or an infinity does not change the value in force; `build()` always gives weights between 0 and 5 | T-QUAL-02 | `tests/collaudo/test_fase3_dati_personali.py` |
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
| R11 | Start times without a plausibility window | See D-F1-04 | T-SEC-02, T-GPX-01 |
| R14 | List of profiles unusable | Message with the location of the file; the window stays usable | T-DATI-01 |
| — | Deletion too wide | Only the names the app puts in the folder; refused if there is anything else, a link or an open profile | T-SEC-04, T-DATI-04 |
| — | Migration without a safety net | Copy of the database in `backup/ponos-v{from}-v{to}.sqlite3` before every migration; without a copy it does not migrate | T-DATI-01 |
| — | Preview that modifies the profile | `peek` opens the database read-only, without migrating or taking the lock | T-UI-03 |
| — | File replacements with different techniques | One single module, `ponos_coach.fsio` | T-DATI-02 |

### The map page (phase 5b, T-SEC-14 and T-SEC-15)

- **Page**: `ponos://app/map.html`, same engine and interceptor as the charts;
  CSP `default-src 'none'`, `connect-src 'none'`, `img-src` only `data:`, `blob:`
  and `ponos://tiles`. Scripts: Leaflet and markercluster from `web/vendor/`
  (hash-checked by test), `qwebchannel.js` from Qt, `map.js` (no `fetch`, no
  `eval`, no `innerHTML`: tested).
- **Tiles**: `ponos://tiles/z/x/y` only (interceptor: integers inside the
  pyramid, no query, fragment or port); the scheme handler asks `TileProxy` in
  a worker pool (at most 256 waiting) and never the network from the page.
- **Channel**: one `QWebChannel` object, five slots taking numbers
  (`pageReady`, `viewChanged`, `activityClicked`, `pointHovered`,
  `pointLeft`). The page names an activity only by its position in the list
  Python sent; every value is validated (`map_messages`) and rejected ones
  are counted, with no effect. JSON cannot carry NaN or infinity: the channel
  turns them into 0, a valid and harmless value. Qt also publishes
  `QObject.deleteLater` on the object: a compromised page could delete the
  bridge and so silence its own map (accepted, no other effect).
- **Positions**: never in logs; the map's last view is not saved (a position in
  `QSettings` would be a file outside the planned folders). The tile cache keeps
  tile addresses (the areas looked at) in the app's cache folder, not in the
  profile.
- **Key**: only the key store and the HTTP adapter see it; the settings dialog
  hides what is typed, clears the field after saving and never shows a saved key.

### Cases closed by the phase 4b acceptance testing (D-F4B)

Defects found by the phase 4b acceptance testing (D-F4B-01…09) and the
behaviour that closes them. The defect tests have the id in the name
(`test_d_f4b_NN_…`) or in a comment; D-F4B-06 also has a test in
`tests/nutrition/test_foods.py`.

| Id | Defect | Behaviour | Test (plan) | Test (file) |
|---|---|---|---|---|
| D-F4B-01 | A woman of 98 years, 100 cm and 20.1 kg (inside the limits of the person) with a 100 g loss goal made the day fail with "macronutrient grams cannot be negative" | Carbohydrate room and protein cap of the day's macros are clamped at zero, so no quantity is negative; every adult inside the limits gets a day, under 18 the day is unavailable by design | T-NUT-02, T-NUT-04 | `tests/collaudo/test_fase4b_modello.py` |
| D-F4B-02 | Declared calories of 50,000 kcal in 20 minutes were taken at face value and inflated the target | A total above 1,500 kcal per hour of moving time is discarded and estimated as if absent | T-NUT-03 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-03 | In an eight-hour ultra the "during" box said 60 g of carbohydrate an hour, but the item held far fewer grams, and dinner kept none | Box and grams agree; lunch and dinner keep at least 1 g/kg of carbohydrate | T-NUT-04 | `tests/collaudo/test_fase4b_modello.py` |
| D-F4B-04 | The recovery after a session with long stops was placed at the end of the moving time, while the person was still out | The end of a session is the start plus the elapsed time | T-NUT-04 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-05 | A date of birth of 1900 and a day in 2035 (135 years) raised an exception | The result is "unavailable" (`AGE_OUT_OF_RANGE`) | T-NUT-01 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-06 | A food table nested 100,000 levels deep exhausted the JSON decoder's recursion | The depth (at most 8) is checked before the decoder; the error is a `FoodTableError` | T-NUT-06 | `tests/collaudo/test_fase4b_alimenti.py`, `tests/nutrition/test_foods.py` |
| D-F4B-07 | A session across the change to summer time had its recovery before its end on the wall clock | The end is computed in UTC and then shown in local time | T-NUT-04 | `tests/collaudo/test_fase4b_giorni.py` |
| D-F4B-08 | The source and licence of the food table (USDA FoodData Central, CC0) were not in the dependency register | Entry "USDA FoodData Central" in `docs/dipendenze.md` | T-NUT-06 | `tests/collaudo/test_fase4b_alimenti.py` |
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
| Unsigned executables | Personal project without a signing certificate. Mitigation: `SHA256SUMS`, README with hash verification and SmartScreen notice. |
| Tokens readable by every process of the user | DPAPI in user scope and the Secret Service protect from other users, not from other processes of the same user. Credential Manager, rejected because it cannot hold the tokens (2560 bytes), would have had the same limit. A password the user must type at every start would go against daily use. The machine is assumed not compromised. Mitigation: logout deletes the tokens. |
| Non-interruptible library timeouts | The timeout belongs to the library (15 s per request, 30 s per login step) and cannot be configured without touching internal parts. Cancellation applies from the next request: a request already sent ends or times out on its own. Mitigation: the journal makes resuming safe at every point. |
| Response or download read entirely into memory by the library before the cap | The cap of 50 MB applies after the read. Mitigation: the 15 s timeout and the ZIP limits downstream (T-SEC-02). |
| DPAPI entropy is not secret | The code is public; the entropy ties the file to the app and the profile, it does not protect from the user themselves. |
| Wrong matching between two almost equal activities (60 s, 5%, same sport) | The imported original file stays; the other activity is not downloaded. As for duplicates between FIT and GPX, better not to lose data. |
| Health data in plain text in the profile folder, protected only by the system permissions | Simplicity and opening with other tools; encrypting requires managing a password and a recovery key. Accepted by the user on 2026-11-05. From phase 3 it also holds height, weight (with the history) and date of birth. Mitigation: folder permissions for the user only (T-SEC-09); the README recommends disk encryption (BitLocker on Windows, LUKS on Ubuntu). |
| Copies of the database in `backup/` and archive files with the same health data | Same risk and same mitigation as the profile folder: same private permissions. |
| Profile folder on a network or in a synchronised folder | The app warns but does not forbid: the user may have a reliable NAS. On these places the SQLite lock and WAL are not guaranteed; a damaged database is reported at opening. |
| Incomplete detection of network and cloud | UNC, remote drives, network mounts, OneDrive, Dropbox, Google Drive and iCloud Drive are recognised by name and variables; an unknown service is not recognised. When in doubt the type is unknown and there is no warning. |
| List of profiles readable by every process of the user | It contains paths and names of profiles, not health data; it is in the user's configuration folder. |
| An attack with the user's permissions can replace the archive and its database together | Integrity protects from errors and partial modifications, not from whoever already controls the account (assumptions). |
| Unrecognised duplicate between FIT and GPX outside the threshold (60 s, 5%) | The activity enters twice: no data lost. Better than deleting a real activity. |
| No secure deletion of data | Beyond the reach of an app on ordinary file systems. |
| Food values not yet verified against FoodData Central | The first version of the table was written without access to the source. Mitigations: `source.verified_on` is empty and the release waits for `experiments/verify_foods.py`; the parser checks energy against macros; the notice says the guidance is general. |
| Guidance built on population formulas (Mifflin-St Jeor, Keytel, Boer, factors without sport) and on self-assessed level | Individual error of hundreds of kcal is possible, and an app cannot measure more. Mitigations: conservative limits of the deficit, no numbers under 18 years, fixed notice, no diagnosis (ADR 008). |
| Declared calories that are wrong but plausible | Under 1,500 kcal per hour they cannot be told from a real effort. Only the net energy (without the rest rate) is added to the expenditure, which reduces the effect. |
| A threshold that is wrong but plausible (a field test, a hard day, a fast downhill window) | It cannot be told from a real effort within the limits of the refusals. Mitigations: the user chooses the activity, the refusals with reasons, the exact undo, the retest every 6–8 weeks; the zones are a training indication, not medical advice (ADR 009). |
| Distance from a GPS or a treadmill sensor with a systematic error | The pace is only as good as the distance; the app cannot measure more. The HR threshold does not depend on it. |
| Food names and nutrition advice only in English until translated; no allergy or diet restriction | User decision of 2026-11-07. The examples are an illustration, not a menu. |

## Open points

- The backup contains health data: format and protection to be decided in
  phase 8.
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
