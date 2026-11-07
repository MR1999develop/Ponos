# 006. Tokens, synchronisation thread, journal and retry policy

- Status: Accepted
- Date: 2026-11-06

## Context

Phase 2 connects Ponos Coach to Garmin Connect (ADR 002) and downloads the
activities. Four choices become hard to change after release: where the tokens
are, on which thread and connection synchronisation runs, what is saved in the
database to be able to resume, and how to behave with Garmin's limits.

This ADR refines the part of ADR 002 that says "the tokens are in the system
keychain" and adds a schema version (v2) to ADR 005. The rest of both remains
valid.

Constraints:

- the access is not official: few requests, no endless loop;
- the tokens give access to the account: they are not in the profile folder or
  in plain text on disk;
- the window does not block during login and download;
- an interruption at any point neither loses nor duplicates activities;
- the database is an index: the original files remain the source of truth.

## Options

### Where the tokens are

1. **Windows: one file per profile encrypted with DPAPI (user scope); Ubuntu:
   Secret Service. No plain fallback.**
   - Pro: no size limit; the text of the tokens never touches the disk in plain
     text; the Secret Service is the standard place on Linux.
   - Con: on Windows the file is managed by the app (format, permissions,
     deletion); DPAPI protects from other users, not from other processes of
     the same user; without Secret Service on Ubuntu the account cannot be
     connected.
2. **Windows Credential Manager through `keyring`.**
   - Pro: no format to manage; the place Windows intends for this.
   - Con: the generic Credential Manager accepts at most 2560 bytes and the
     Garmin tokens are longer (the phase 0 spike measures their size): saving
     would fail or truncate them.
3. **File encrypted with a key of the app or with a password of the user.**
   - Pro: same behaviour on every system.
   - Con: a key in the code or next to the file is obfuscation; one more
     password for the user goes against "login is done once" and requires a
     recovery key.
4. **Plain file with private permissions, or plain fallback when the keychain
   is missing.**
   - Pro: works everywhere.
   - Con: the secret stays readable from backups, antivirus and synchronisation
     tools; it is the silent fallback the rules forbid.

### Thread and connection of the synchronisation

1. **One thread per account operation, with its own SQLite connection opened on
   the profile.**
   - Pro: the window and the import stay available; no connection shared
     between threads; WAL allows reading while writing.
   - Con: two connections can contend for the write (wait of 5 s, then
     `StorageBusyError`); results return to the UI with queued signals.
2. **The data thread's connection, with a lock.**
   - Pro: a single connection.
   - Con: import, charts and synchronisation wait for each other; a slow
     download holds up the rest.
3. **A child process for the synchronisation.**
   - Pro: total isolation from the unofficial library.
   - Con: process management (job, closing, communication channel)
     disproportionate for one library.

### Journal and cursors

1. **Tables in schema v2: `sync_journal` (one row per remote activity with
   state and cause), `sync_cursor` (point reached in each listing),
   `sync_state` (end of the last synchronisation).**
   - Pro: resuming is a fact of the database, in the same transaction as the
     index; failed entries have a cause and a count; the listing restarts from
     the last page.
   - Con: a migration (v1 → v2) with a safety copy; the `CHECK`s and the closed
     sets must be kept aligned with the domain enums.
2. **No journal: Garmin's listing is compared with the `activities` table at
   every run.**
   - Pro: no new table.
   - Con: every run lists the whole history again; an activity that fails
     forever is downloaded again at every run; no resume point for the listing.
3. **A state file (JSON) next to the database.**
   - Pro: no migration.
   - Con: two sources of truth to keep consistent; no transaction in common with
     the index.

### Pauses, backoff and retries

1. **Own policy (`SyncPolicy`), with the library at zero retries.**
   - Pro: a single level of retries, with visible values, injected clock and
     jitter, so testable without sleeping.
   - Con: the values are guesses until there is real experience with Garmin;
     they must be reviewed with the result of the spike.
2. **Library retries (default `retry_attempts`).**
   - Pro: no code.
   - Con: two levels of retries multiply; waits that the app neither sees nor
     can cancel.
3. **No retry: the first error stops the run.**
   - Pro: maximum caution towards Garmin.
   - Con: an isolated timeout interrupts a long synchronisation.

### Activities already imported from files

1. **Matching before the download: same start time (within 60 s), same sport
   and duration within 5%, on an entry without a Garmin id.**
   - Pro: no useless requests; no duplicates.
   - Con: two very similar activities of the same sport can be matched wrongly;
     the rule is the same as for duplicates between FIT and GPX (ADR 005).
2. **Always download and let the identity eliminate the duplicate.**
   - Pro: no heuristic.
   - Con: a re-exported FIT does not have the same identity as the original
     imported from the watch; the whole history is downloaded even if already
     present.
3. **Ask the user.**
   - Pro: no error.
   - Con: thousands of questions at the first synchronisation.

## Decision

### Tokens

- Option 1. The single point of choice is `create_token_store`
  (`platform/token_store.py`); the port is `TokenStore` (`save`, `load`,
  `delete`), by profile id. The tokens are `SessionTokens`, an opaque string
  whose `repr` does not show the content.
- Windows: `<app local data folder>/secrets/<id>.bin`, private folder, atomic
  write. Content: magic, format version and DPAPI blob. An entropy tied to the
  app and to the profile id prevents the file of one profile from being
  decrypted in another. The entropy is not a secret: the code is public.
- Ubuntu: Secret Service through `keyring`, service of the app, user = profile
  id, value with a version prefix. A missing, locked or plain-text backend is
  `TokenStoreUnavailableError`: never a fallback to a file.
- Without a usable store the account commands are disabled with the reason;
  file import stays available.
- The tokens are never in the profile folder or in the backup. A profile moved
  to another PC requires a new login.
- An unreadable or altered file is `TokenStoreCorruptError`: the app asks for a
  new login and replaces it.
- The keychain is readable by the user's processes: risk accepted in the threat
  model.

### Thread and connection

- Option 1. Token check, login, logout and synchronisation are operations of a
  single controller (`SyncController`), one at a time, each in a thread of its
  own.
- The synchronisation opens its own connection to the profile and closes it at
  the end; the archive is written before the transaction, which lasts only the
  time of the record.
- Cancellation is cooperative (`threading.Event`), checked before every
  request, entry and page; the pauses are interruptible.
- The password and the verification code go only to `GarminAccount` and are not
  kept. The wait for the code has a cap of 5 minutes, then counts as cancelled
  and nothing is saved.
- The thread does not raise towards the UI: it returns a result
  (`SyncFinished` or `SyncNotStarted`); an unexpected failure is recorded with
  only the type.

### Journal and cursors

- Option 1, schema v2. Columns and constraints are in
  `storage/sqlite_schema.py` (single source); this ADR does not repeat them.
  The v1 → v2 migration follows the procedure of ADR 005 (copy in
  `backup/ponos-v1-v2.sqlite3`).
- States: to download, downloaded, matched, retryable, failed. There is no "in
  progress" state: an interruption leaves the entry to download.
- The cursor is saved at every page, in the same transaction as the entries. A
  listing ends with a short or empty page, never at the total declared by
  Garmin (which serves only the progress).
- Listings: full (first time), incremental (from the most recent, stopping at a
  whole page of already known ids), full check (lists everything again and
  re-arms the entries failed for exhausted attempts).
- An entry failed for a retryable cause becomes definitive after a fixed number
  of failed synchronisations. A run stopped by rate limit or by an unreachable
  service does not consume attempts.

### Pause and retry policy

- Option 1. The values (pause with jitter, exponential backoff with a cap,
  maximum number of attempts per request and per activity, page size) are
  project defaults in `SyncPolicy` (`activities/domain/sync_policy.py`) and in
  the project skill; they are not repeated here.
- A wait suggested by Garmin (`Retry-After`) raises the minimum of the wait; if
  it exceeds the cap it does not wait and the synchronisation stops with a
  message. An unreadable value is ignored; a negative or non-finite one means
  "implausible wait", that is a synchronisation that stops, never an error or a
  long pause.
- After a few entries in a row failed for an unreachable service it stops.
- The library is configured at zero retries.
- Jitter and clock are injected.

### Matching

- Option 1. Matching links the Garmin id to the existing record and marks the
  entry as matched; the origin of the record stays that of the import.
- The downloaded file must match the listing entry (same rule as duplicates); if
  it is another activity the entry fails definitively and nothing is recorded.
- Activities already in the profile do not count as new in the summary and in
  the notification.

## Consequences

- On Windows the app owns the format of the token file: a change needs a new
  format version and a test that reads the previous one.
- Without Secret Service on Ubuntu the account is not connected. The message
  says so and points to file import.
- Two connections to the database: a lock wait over 5 s stops the
  synchronisation with a clear error and without touching anything.
- A request already sent cannot be interrupted before the library's timeout (15
  s per request, 30 s per login step); cancellation applies from the next
  request.
- The policy values are guesses; changing them does not change the on-disk
  format and does not require an ADR. Changing the states or the causes
  requires a migration.
- A wrong matching (two almost equal activities) links an id to the wrong
  record and the other is not downloaded; the user does not lose the original
  file already imported. Accepted.
- A change of the place of the tokens or of the meaning of the states requires
  a new ADR.

## How compliance is verified

- Tokens: `TokenStore` contract on the fake and on the real adapters
  (`tests/contracts/contract_tokens.py`, `tests/platform/test_token_store.py`);
  T-SEC-07 (with a placeholder token, nothing in the profile folder, in the
  logs, in temporary files, after login, synchronisation, kill and logout;
  logout deletes; the file of another profile is not decrypted) in
  `tests/collaudo/test_fase2_segreti.py`.
- Log: T-SEC-08 on the whole synchronisation, in the same file.
- Thread and cancellation: T-SYNC-06 (stopped within 1 s) in
  `tests/activities/test_sync_errors.py` and
  `tests/collaudo/test_fase2_sincronizzazione.py`; T-UI-02 in
  `tests/ui/test_garmin_window.py` and `tests/collaudo/test_fase2_prestazioni.py`.
- Journal and resume: T-SYNC-01 (full then incremental; 5,000 activities in
  `tests/activities/test_sync_volume.py`, 20,000 in
  `tests/collaudo/test_fase2_prestazioni.py`); T-SYNC-02 with a real process
  kill (`tests/collaudo/test_sync_kill.py`); T-DATI-01 with the frozen v1
  database and a test that compares the closed sets of v2 with the enums.
- Policy: T-SYNC-03 with a fake clock (`tests/activities/test_sync_errors.py`)
  and with hostile `Retry-After` values
  (`tests/collaudo/test_fase2_garmin_ostile.py`); T-SYNC-04 for expired tokens
  and login (`tests/collaudo/test_fase2_sincronizzazione.py`).
- Matching: T-SYNC-05 (`tests/activities/test_sync.py`); the case of the file of
  another activity is D-F2-03.
- Hostile responses: T-SEC-03.
- `lint-imports`: only `garmin` imports `garminconnect`.

## Implementation note 2026-11-06

Result of the spike with a real account (`experiments/garmin_spike.md`):

- The serialised token is 4510 bytes in UTF-16, over the Credential Manager
  limit: this confirms that the tokens are not kept there but protected with
  DPAPI in a file.
- The login did not require MFA (the path with a code remains supported).
- The start of the FIT file matches the start of the listing (difference 0 s).
