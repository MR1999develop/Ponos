# Profile backup file

A backup is one file with the suffix `.ponosbackup` that holds a whole profile:
its database and the original activity files. It is made by the use case
`BackupProfile` and read by `RestoreProfile` (`profiles/backup.py`); the format
is defined once, in `profiles/domain/backup.py`, and written and read by
`storage/profile_backup.py`.

## Layout

The file is a ZIP archive (deflate) with exactly these entries and no others:

| Entry | Content |
|---|---|
| `ponos.sqlite3` | Snapshot of the profile database, made with the SQLite backup API (consistent even if the profile is open) and checked with `quick_check`. |
| `archivio/<year>/<sha256>.fit` or `.gpx` | The original activity files, as in the profile's archive. |
| `manifest.json` | UTF-8 JSON, described below. |

Never in a backup: the Garmin tokens (they live in the system keyring, not in the
profile folder), the lock file, the `backup/` folder with the pre-migration copies,
temporary files and anything with a name outside the list above.

## Manifest

```json
{
 "format": "ponos-coach-profile-backup",
 "format_version": 1,
 "created_utc": "2026-11-08T10:30:00Z",
 "app_version": "0.1.0",
 "profile_id": "<uuid v4>",
 "schema_version": 7,
 "files": [
  {"path": "ponos.sqlite3", "size_bytes": 4096, "sha256": "<64 hex digits>"},
  {"path": "archivio/2026/<64 hex digits>.fit", "size_bytes": 1234, "sha256": "<64 hex digits>"}
 ]
}
```

- `format_version` is the version of this format (now 1). A reader refuses a newer
  one with a message that asks to update the app; unknown keys of the same version
  are ignored.
- `schema_version` is the `user_version` of the database inside; it must match
  the database, whose profile id must match `profile_id`.
- `files` lists every other entry of the ZIP, once, with its size and SHA-256, in
  path order; exactly one is the database.

## Writing

The ZIP is built in a private temporary folder next to the destination and
renamed to its name only when complete (flush, `fsync`, rename, directory
sync). A backup never overwrites a file and is never written inside the profile
folder. If anything fails no file is left.

## Restoring

A backup is restored only into a folder that does not exist or is empty (apart
from the lock file), never over an existing profile. The restored profile keeps
its id and is not added to the profile list by the restore itself.

The file is untrusted input. Before extracting anything the reader checks the
central directory, the number of entries, the declared sizes, the compression
ratio and duplicate names (the same rules as for the ZIPs downloaded from
Garmin), then that the entries are exactly those of the manifest with the sizes
it declares. Entry names come from a closed list (`ponos.sqlite3` and
`archivio/<4 digits>/<64 hex digits>.fit|gpx`); a name from the file is never
used as a path. Each file is streamed to an exclusive temporary file, checked
against its SHA-256, flushed and renamed; the database goes last and is then
opened as the app opens a profile (integrity check, format version, migration
with its own pre-migration copy). If anything fails, everything the restore wrote
is removed and the folder is removed if the restore created it.

| Limit | Value |
|---|---|
| Files of the profile | 100,000 |
| Sum of the sizes | 20 GiB |
| Database | 8 GiB |
| One activity file | 50 MiB |
| Manifest | 32 MiB |
| Compression ratio of an entry | 1,000 |

Why a restore can be refused (`BackupProblem`): not a backup (not a ZIP, no
manifest, wrong format), a limit exceeded, a file that differs from the manifest,
an entry outside the closed list or encrypted, a database that is not an intact
profile. A backup of a newer format or with a newer database is a separate error
(`BackupTooNewError`).
