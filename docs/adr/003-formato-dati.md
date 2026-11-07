# 003. On-disk data format

- Status: Accepted; archive path and GPX identity replaced by ADR 005
- Date: 2026-11-05

## Context

The user's data (activities, parameters, goals, plans, injuries) must survive
crashes and moves. Each profile has a folder chosen by the user. The saved
format is hard to change after release. Activities come from Garmin, from
files imported by hand and from folders: the same activity must not enter
twice.

## Options

1. **Original FIT files as the source of truth and SQLite as index and user
   data, in one folder per profile.**
   - Pro: the original data is not lost and the derived values (loads, PMC) are
     regenerated; the files can be opened with other tools; the database stays
     small.
   - Con: two stores to keep consistent (strict write order); more files in the
     folder.
2. **SQLite only, with the FIT files as BLOBs.**
   - Pro: a single file, a single transaction.
   - Con: large database (years of activities); a corruption hits everything;
     backup and integrity check of the single FIT files are more awkward.
3. **One JSON file per activity.**
   - Pro: simple to read.
   - Con: loses the original data or duplicates it; no queries or
     transactions; consistency between files is hard.

## Decision

Option 1. In the profile folder: `ponos.sqlite3` (WAL, foreign keys,
`user_version` and migrations), `fit/<year>/<activity_id>.fit`, `backup/`,
`ponos.lock`. Each FIT has a recorded SHA-256. A FIT is written to a temporary
file, with flush and rename; the SQLite record is written after the file.

The identity of an activity is the `file_id` of the FIT (manufacturer, serial
number, creation time): it is the same for the same file downloaded from
Garmin or copied from the watch. The Garmin id is added when known.

The Garmin tokens are never in the folder: they are in the system keychain,
tied to the profile id. Loads and the PMC are derived.

## Consequences

- The folder is self-sufficient and can be moved or opened from another PC.
- It contains health data in plain text (see accepted risks in
  `docs/threat-model.md`).
- At restart archive and database are reconciled: no record without a file,
  no orphan temporary file.
- Changing the format requires a versioned migration (expand-contract) and a
  new ADR if the folder structure changes.
- Folders on a network or synchronised disk are at risk: warning and lock.

## How compliance is verified

- T-DATI-01 (migrations), T-DATI-02 (interruption at every point of the
  write), T-DATI-03 (altered FIT), T-DATI-04 (lock), T-DATI-05 (backup and
  restore), T-IMP-03 (idempotence).
- T-SEC-07: the token does not appear in the profile folder.
- T-SEC-04: no external data in the names of the archive files.
