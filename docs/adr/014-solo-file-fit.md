# 014. Only FIT files

- Status: Accepted
- Date: 2026-11-09

## Context

Phase 1 accepted FIT, GPX and ZIP files (ADR 003, ADR 005). On 2026-11-09 the
user asked to work only with `.fit` files and to drop the rest. GPX needed its own
reader (an XML parser, `defusedxml`), its own identity (SHA-256 of the bytes, no
`file_id`), a cross-format duplicate rule and a lot of hostile-input tests; ZIP
import needed a second boundary (decompression limits, entry names).

Constraints:

- user files are never lost without a safety copy, and an interrupted change must
  leave the profile as it was;
- the file in the archive is the source of truth: a record must never point to a
  missing file (an orphan file is harmless);
- Garmin still sends the original of an activity as a ZIP that contains the FIT, and
  the profile backup is a ZIP: reading ZIP stays (`zipread`), only for those two uses. The
  backup format still accepts `.gpx` archive entries, so that a backup made before this
  change can be restored (the restored profile is migrated when opened).

## Options

1. **Keep GPX read-only for old profiles.**
   - Pro: nothing is removed.
   - Contra: the XML parser and the second identity stay for data that no new
     import can produce.
2. **Remove GPX activities with a schema migration (chosen, the user's decision).**
   - Pro: one format, one identity, no XML attack surface.
   - Contra: activities imported from GPX are lost from the profile (the originals
     the user still has can be converted or re-downloaded as FIT from Garmin).

## Decision

- Import accepts only `.fit` (files and folders). Other files in a folder are
  ignored; a GPX or ZIP chosen by hand is reported as an unsupported format.
  `FileFormat` has the single value `fit`; `GpxIdentity`, the GPX reader,
  `defusedxml`, the cross-format duplicate rule and the import of ZIP files are
  removed. A Garmin ZIP that contains only a GPX fails as an unsupported format.
- **Schema v11** (`storage/sqlite_schema.py`): in one transaction, the GPX activities
  leave `activities`, `file_format` admits only `fit` (the table is rebuilt: SQLite
  cannot alter a CHECK), the same for the `threshold_test` keys (`fit:` only, the
  tests of removed activities go), the load cache is emptied (derived) and the sync
  journal entries that pointed to a removed activity go back to `pending`.
- **Order**: the existing mechanism copies the database to
  `backup/ponos-v10-v11.sqlite3` before any migration (without a copy nothing
  migrates); then the transaction; only after the commit the GPX files are removed
  from the archive, and only if the stored path has the exact archive form
  (`<year>/<sha256>.gpx`). A crash between the commit and the removal leaves
  harmless orphan files. Nothing remembers them afterwards (accepted).
- The user is told once, when the profile opens, how many activities were removed
  (`OpenProfile.removed_unsupported_activities`, shown by the main window). The
  count is not stored: if the app dies before showing it, the copy in `backup/`
  is the record.

## How it is checked

`tests/collaudo/test_fase8_solo_fit.py`: a v10 profile with FIT and GPX
activities and files becomes a FIT-only v11 profile (records, archive files, copy,
journal, cache, constraint, other tables intact); the migration killed at every
statement leaves v10 and every file untouched and then resumes; a path outside the
archive form is never removed. `tests/storage/test_import_scan.py` and
`tests/collaudo/test_import_pila_reale.py`: only `.fit` is read.
