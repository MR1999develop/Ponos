# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synchronisation test process, to be started with `python -I collaudo_sync_worker.py ...`.

`sync <profile-folder> <how-many> <page> <point>`: opens the profile (real lock, SQLite and
archive), synchronises with a fake Garmin of `how-many` runs (pages of `page` entries, real FIT in
ZIP) and stops where `<point>` says, waiting to be killed by the test:

- `none`: never (prints `progress <n>` after each entry processed and `finished <downloaded>`);
- `list:<n>`: before answering the n-th request for a list page;
- `cursor:<n>`: in the n-th cursor write, with the page entries already added but the
  transaction not committed;
- `download:<n>`: before answering the n-th download;
- `before_add:<n>`: after the archive, before the record of the n-th activity;
- `after_add:<n>`: record written but transaction not committed;
- `update:<n>`: in the n-th state change of an entry, inside the transaction.

Talks to the test line by line: `ready <pid>`, `blocked <point>`, `progress <n>`, `finished <n>`.
"""

from __future__ import annotations

import os
import random
import sys
from collections.abc import Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the root, for `tests.*`

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityPage,
    ActivityRecord,
    GarminActivityId,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RemoteActivity,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.ports import ActivityIndex, ActivitySource, SyncJournal
from ponos_coach.activities.sync import (
    SyncPhase,
    SyncPorts,
    SyncProgress,
    synchronize_activities,
)
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.fakes.garmin import FakeActivitySource, make_remote_history
from tests.fixtures.generators.sync_harness import extract_original

_FIXED_NOW = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


def _say(*words: object) -> None:
    print(*words, flush=True)  # noqa: T201 - the dialogue with the test is on stdout


def _wait_for_death() -> None:
    """Stays still until the test kills the process; if the test closes, it exits anyway."""
    sys.stdin.read()
    os._exit(3)


class _Point:
    """Where to stop: `kind:n` or `none`."""

    def __init__(self, spec: str) -> None:
        self.kind, _, number = spec.partition(":")
        self.number = int(number) if number else 0
        self._seen: dict[str, int] = {}

    def reached(self, kind: str) -> bool:
        """Counts one pass per `kind`; true (and stops) if it is the chosen one."""
        self._seen[kind] = self._seen.get(kind, 0) + 1
        return kind == self.kind and self._seen[kind] == self.number

    def block_if(self, kind: str) -> None:
        if self.reached(kind):
            _say("blocked", kind)
            _wait_for_death()


class _BlockingSource:
    def __init__(self, inner: FakeActivitySource, point: _Point) -> None:
        self._inner = inner
        self._point = point

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        self._point.block_if("list")
        return self._inner.list_page(offset, limit)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        self._point.block_if("download")
        return self._inner.download_original(activity_id, max_bytes)


class _BlockingIndex:
    def __init__(self, inner: ActivityIndex, point: _Point) -> None:
        self._inner = inner
        self._point = point

    def transaction(self) -> AbstractContextManager[None]:
        return self._inner.transaction()

    def exists(self, identity: ActivityIdentity) -> bool:
        return self._inner.exists(identity)

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        return self._inner.get(identity)

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        return self._inner.find_possible_duplicates(start, elapsed_s)

    def add(self, record: ActivityRecord) -> None:
        self._point.block_if("before_add")
        self._inner.add(record)
        self._point.block_if("after_add")

    def list_by_start(self) -> Sequence[ActivityRecord]:
        return self._inner.list_by_start()

    def list_started_between(self, first: datetime, last: datetime) -> Sequence[ActivityRecord]:
        return self._inner.list_started_between(first, last)

    def first_start_at_or_after(self, instant: datetime) -> datetime | None:
        return self._inner.first_start_at_or_after(instant)

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> ActivityRecord | None:
        return self._inner.find_by_garmin_id(garmin_id)

    def link_garmin_id(self, identity: ActivityIdentity, garmin_id: GarminActivityId) -> None:
        self._inner.link_garmin_id(identity, garmin_id)


class _BlockingJournal:
    def __init__(self, inner: SyncJournal, point: _Point) -> None:
        self._inner = inner
        self._point = point

    def load_cursor(self, kind: ListingKind) -> ListingCursor | None:
        return self._inner.load_cursor(kind)

    def save_cursor(self, kind: ListingKind, cursor: ListingCursor) -> None:
        self._point.block_if("cursor")
        self._inner.save_cursor(kind, cursor)

    def known_ids(self, ids: Collection[GarminActivityId]) -> frozenset[GarminActivityId]:
        return self._inner.known_ids(ids)

    def add_pending(self, items: Sequence[RemoteActivity], now: datetime) -> int:
        return self._inner.add_pending(items, now)

    def next_to_process(self, limit: int) -> Sequence[JournalEntry]:
        return self._inner.next_to_process(limit)

    def next_exhausted(self, limit: int) -> Sequence[JournalEntry]:
        return self._inner.next_exhausted(limit)

    def get(self, garmin_id: GarminActivityId) -> JournalEntry | None:
        return self._inner.get(garmin_id)

    def update(self, entry: JournalEntry) -> None:
        self._point.block_if("update")
        self._inner.update(entry)

    def counts(self) -> Mapping[SyncStatus, int]:
        return self._inner.counts()

    def last_completed_at(self) -> datetime | None:
        return self._inner.last_completed_at()

    def record_completed(self, when: datetime) -> None:
        self._inner.record_completed(when)


class _Clock:
    def now(self) -> datetime:
        return _FIXED_NOW

    def monotonic(self) -> float:
        return 0.0


def _no_wait(_seconds: float) -> bool:
    """No real waiting: the test does not sleep."""
    return False


def _sync(profile_folder: str, count: int, page_size: int, spec: str) -> None:
    point = _Point(spec)
    folder = Path(profile_folder)
    lock = LocalProfileFolders(lambda _folder: FolderKind.LOCAL).acquire_lock(folder)
    opened = SqliteProfileStorage().open(folder)
    source: ActivitySource = _BlockingSource(FakeActivitySource(make_remote_history(count)), point)
    ports = SyncPorts(
        source,
        _BlockingIndex(opened.activities, point),
        _BlockingJournal(opened.sync_journal, point),
        FileArchive.in_profile_folder(folder),
        extract_original,
        decode_stored_activity,
        _Clock(),
        _no_wait,
        random.Random(1),
        SyncPolicy(page_size=page_size),
    )

    def report(progress: SyncProgress) -> None:
        if progress.phase is SyncPhase.DOWNLOADING and progress.done:
            _say("progress", progress.done)

    _say("ready", os.getpid())
    outcome = synchronize_activities(ports, report=report)
    _say("finished", outcome.downloaded)
    opened.close()
    lock.release()


def main(argv: Sequence[str]) -> int:
    command, *args = argv
    if command == "sync":
        _sync(args[0], int(args[1]), int(args[2]), args[3])
        return 0
    print(f"collaudo_sync_worker: comando sconosciuto: {command}", file=sys.stderr)  # noqa: T201
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
