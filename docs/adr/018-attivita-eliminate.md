# 018. A deleted activity leaves a tombstone, and its file stays in the archive

- Status: Accepted
- Date: 2026-11-10

## Context

The user asked (2026-11-10): "from the activities list, with the right button, let me delete the
activity (it must not come back with the next synchronisation)". The answers to the questions:

- The FIT file stays in the profile archive: deleting is reversible, with Ctrl+Z at once and with
  a window "Deleted activities..." afterwards.
- If the user imports the same file by hand (chosen by them), the activity comes back and the
  import summary says so. The synchronisation with Garmin and any automatic import skip it always.

Until now the activity index was derived from the archive (the files are the source of truth, the
index can be rebuilt) and the synchronisation journal remembered each remote activity as
`downloaded`, `matched`, `failed`... Deleting an activity is a decision of the user that the files
cannot tell: a profile rebuilt from the archive would bring every deleted activity back. So
something new has to be saved, and it is saved in the profile database and in the backups: a
format decision that is hard to undo (a table, and a state of the journal under `CHECK`s).

What brings a deleted activity back, if nothing is done:

1. A synchronisation lists a remote activity whose entry is `pending` (an activity imported from a
   file, deleted, and listed later) and downloads it: the activity returns.
2. A folder import finds its file: the activity returns.
3. A downloaded file has the identity of the deleted activity (the same FIT under another Garmin
   id).

Constraints: the dominio stays pure; the user is never asked "Are you sure?" (an undo instead);
the deletion is all or nothing; the identity is the one already used for duplicates (the FIT
`file_id`, and the Garmin id); health data (positions, heart rate) are not copied anywhere new.

## Options

### Where the decision is recorded

1. **A flag `deleted` on the `activities` row.**
   - Pro: one table; the file reference is already there.
   - Con: every query of the index (list, load, map, calendar, plan, duplicates) must remember to
     filter, and one that forgets shows a deleted activity; the cached load, the track box and the
     polyline (positions) stay in the row; "an identity is in the index" would stop meaning "the
     activity is in the profile".
2. **Only in the synchronisation journal** (a new state "ignored by the user").
   - Pro: nothing else changes.
   - Con: it does not cover an activity that never went through the journal (imported from a
     file), a folder import, or a file that arrives under another id.
3. **A table of its own, `deleted_activity` (the tombstone), and the row leaves `activities`.**
   - Pro: everything that reads the index stays correct without change (a deleted activity is not
     there, with its cached load, box and polyline, which are derived and go with the row); the
     tombstone has the identity, the Garmin id and just what the "Deleted activities" list shows
     (sport, start, elapsed time, distance), the reference to the file and what is needed to put
     the activity back as it was (origin, import instant); nothing of the track or the heart rate.
   - Con: a new table, a new port and a second place that the synchronisation and the import must
     look at; the invariant "an identity is in one table, never both" has to be held.

Chosen: 3.

### How the invariant is held

1. **In the use cases only.** A code path that forgets to look at the tombstones brings the
   activity back with no error.
2. **In the database too, with two triggers**: an `INSERT` into `activities` of a deleted identity
   aborts, and an `INSERT` into `deleted_activity` of an indexed identity aborts. A path that
   forgets fails loudly (a failed file in an import, a refused write in a sync) instead of
   resurrecting the activity. Chosen, with a contract test on the fake and on SQLite.

### What a synchronisation does with an entry whose activity is deleted

1. **Leave it `pending`.** Then it is "to download" for ever, counts in the progress and is looked
   at at every round.
2. **Mark it `failed`.** A choice of the user is not an error, and a `failed` entry is rearmed by
   the "Full check".
3. **A new final state `skipped`.** Chosen. It is neither a failure nor work to do; it never moves.
   The journal table is rebuilt in the migration (SQLite cannot change a `CHECK`), as in v13.

The entry is skipped before downloading when the list is enough to know: the tombstone has the same
Garmin id, or (an activity imported from a file has no Garmin id) the same sport, a start within 60
s and a duration within 5% (the rule for duplicates, `find_matching_deleted`). Otherwise the file
is downloaded and, if its identity is a deleted one, nothing is recorded and the entry is skipped.

### Which imports bring it back

A folder is examined and imported as a whole: the user did not choose each file, and a folder of
the whole history would resurrect everything deleted. The files the user chose by hand (the dialog
or dropping them) are a decision of the user. So `import_activities` takes a policy: `LEAVE_OUT`
(a folder; the result is `left_out`, counted apart and said in the summary) or `BRING_BACK` (chosen
files; the tombstone is removed and the activity returns with the origin, import instant and Garmin
id it had: `brought_back`).

## Decision

- Schema v19 (`storage/schema_deleted_activity.py`, `DELETED_ACTIVITY_MIGRATION`): the table
  `deleted_activity` (`STRICT`, `CHECK`s as `activities`, no position and no heart rate), the two
  triggers, and `sync_journal` rebuilt with the state `skipped`. A rebuild of `activities` in a
  later migration must create the trigger again.
- `activities/domain/deleted.py` (`DeletedActivity`, `find_matching_deleted`), the port
  `DeletedActivities` and `ActivityIndex.remove` in `activities/ports.py`, the use cases
  `DeleteActivities` and `RestoreActivities` in `activities/deletion.py`. Deleting is one
  transaction: the row and its cached load out, the tombstone in; the file is not touched.
  Restoring reads the archived files again with the decoder of the import, outside the
  transaction; then the activities whose file is good go back in one transaction. A file that is
  gone or damaged leaves its activity deleted and does not stop the others; a failure of the
  database restores nothing (review of 2026-11-11: one transaction per activity left some
  restored while the job reported a failure).
- The archive is never written or removed by a deletion. The file is in the profile backups too.
- The undo is a `QUndoStack` of the Activities list (`ActivityDeletionUi`); restoring goes through
  the same stack. The shortcuts (Del, Ctrl+Z, Ctrl+Shift+Z) act while the list has the focus.
- A threshold test applied from an activity stays in the history after the activity is deleted
  (it is a record of its own, keyed by identity, with no foreign key).

## Consequences

- A profile older than v19 is migrated on opening, with the usual copy in `backup/`. The journal
  rows are copied as they are; no activity is marked deleted.
- A program older than v19 refuses a v19 profile (format too new), so a deleted activity cannot be
  resurrected by an older version.
- Deleting does not free disk space and is not secure deletion: the file stays, by decision of the
  user (the restore needs it). An accepted risk in the threat model.
- Deleted activities are not in the load, the charts, the map, the calendar or the plan. They are
  recomputed after every deletion or restore, as after an import.
- A restored activity whose file was removed from the archive stays deleted and says why; the user
  can choose the file with "Import files".

## How we verify it

- Acceptance: `tests/activities/test_deletion.py` (a deleted activity does not come back with the
  next synchronisation; an imported-then-deleted one is skipped, not downloaded, not a failure and
  not left to do) and `tests/ui/test_activity_deletion.py` (the same through the window).
- Contract of the tombstones and of the index removal, on the fake and on SQLite:
  `tests/contracts/contract_deleted.py`.
- Migration from the previous version and the frozen text of the migration:
  `tests/storage/test_deleted_activity_store.py`.
- Imports and restore: `tests/activities/test_deletion.py`; the summary:
  `tests/ui/test_import_report.py`.
