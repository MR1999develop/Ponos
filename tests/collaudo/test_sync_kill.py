# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SYNC-02: a real kill during the list and during the downloads, then the resume.

A subprocess opens the profile (real lock, SQLite and archive) and synchronises with a fake Garmin
(real FIT in ZIP); the test kills it, without cleanup, at a precise point (before a list
page, halfway through writing a page and its cursor, before a download, between the
archive and the record, with the record written and the transaction open) or at an arbitrary instant
after N entries, and then reopens the profile as the app would on restart.

Oracles after the kill: lock free; database intact; journal and index agree (every record has
a `DOWNLOADED` entry and vice versa), the journal entries are exactly those of the pages with
the confirmed cursor (page and cursor are one transaction); every record has its file in the
archive with the right SHA-256; at most one file without a record and one temporary (the one in
flight).
After the resume: all the activities present exactly once, no repeated download of what
was confirmed, no orphan or temporary file, a second round changes nothing.
"""

from __future__ import annotations

import hashlib
import random
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import ListingKind, SyncPolicy, SyncStatus
from ponos_coach.activities.sync import SyncOutcome, SyncPorts, synchronize_activities
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import FolderKind
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.processes import Worker
from tests.collaudo.support import create_profile, stored_files, temporaries
from tests.fakes.garmin import FakeActivitySource, make_remote_history
from tests.fixtures.generators.sync_harness import (
    FakeSyncClock,
    MidpointRandom,
    extract_original,
)

SYNC_WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / ("collaudo_sync_worker.py")
)
COUNT = 10
PAGE = 3  # four pages: 3 + 3 + 3 + 1
LIST_CALLS = 4

DEFAULT_POINTS = (
    "list:1",
    "list:3",
    "cursor:2",
    "download:1",
    "download:5",
    "before_add:4",
    "after_add:4",
    "update:6",
)
ALL_POINTS = (
    *(f"list:{n}" for n in range(1, LIST_CALLS + 1)),
    *(f"cursor:{n}" for n in range(1, LIST_CALLS + 1)),
    *(f"download:{n}" for n in range(1, COUNT + 1)),
    *(f"before_add:{n}" for n in range(1, COUNT + 1)),
    *(f"after_add:{n}" for n in range(1, COUNT + 1)),
    *(f"update:{n}" for n in range(1, COUNT + 1)),
)


class SyncWorker(Worker):
    """Like `Worker`, but starts `collaudo_sync_worker.py` (which `Worker` does not know)."""

    def __init__(self, *arguments: str | Path | int) -> None:
        self._process = subprocess.Popen(  # noqa: S603 - interprete e script fissi
            [sys.executable, "-I", str(SYNC_WORKER), *map(str, arguments)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.pid = 0
        self._lines_seen: list[str] = []


def kill_at(profile_folder: Path, point: str) -> None:
    """Starts the synchronisation, lets it stop at the given point and kills it."""
    with SyncWorker("sync", profile_folder, COUNT, PAGE, point) as worker:
        worker.expect_ready()
        worker.read_until("blocked")
        worker.kill()


def _wait_without_sleeping(clock: FakeSyncClock, seconds: float) -> bool:
    clock.advance(seconds)
    return False


@contextmanager
def opened_profile(profile_folder: Path) -> Iterator[SyncPorts]:
    """The profile as the app opens it on restart, with a new fake Garmin and the real archive."""
    folders = LocalProfileFolders(lambda _folder: FolderKind.LOCAL)
    lock = folders.acquire_lock(profile_folder)  # T-DATI-04: the dead process's lock is free
    opened = SqliteProfileStorage().open(profile_folder)  # WAL recovered, integrity verified
    clock = FakeSyncClock()
    fake = FakeActivitySource(make_remote_history(COUNT))
    try:
        yield SyncPorts(
            fake,
            opened.activities,
            opened.deleted_activities,
            opened.sync_journal,
            FileArchive.in_profile_folder(profile_folder),
            extract_original,
            decode_stored_activity,
            clock,
            lambda seconds: _wait_without_sleeping(clock, seconds),
            MidpointRandom(),
            SyncPolicy(page_size=PAGE),
        )
    finally:
        opened.close()
        lock.release()


def check_after_crash(profile_folder: Path) -> int:
    """Checks consistency after the kill; returns how many activities were confirmed."""
    with opened_profile(profile_folder) as ports:
        records = list(ports.index.list_by_start())
        counts = ports.journal.counts()
        cursor = ports.journal.load_cursor(ListingKind.FULL)
        archive = ports.archive
        for record in records:
            archive.verify(record.archived)  # no record without a file, no altered file
        done = len(records)
        # Index and journal are a single transaction: they always agree.
        assert counts[SyncStatus.DOWNLOADED] == done
        assert counts[SyncStatus.PENDING] == sum(counts.values()) - done
        # The entries are exactly those of the pages with the confirmed cursor.
        assert sum(counts.values()) == (0 if cursor is None else cursor.next_offset)
    referenced = {profile_folder / "archivio" / r.archived.relative_path for r in records}
    assert referenced <= set(stored_files(profile_folder))
    assert len(set(stored_files(profile_folder)) - referenced) <= 1  # at most the one in flight
    assert len(temporaries(profile_folder)) <= 1
    return done


def resume(profile_folder: Path, done: int) -> SyncOutcome:
    """Resumes like the app: cleans the temporary files, synchronises, checks that everything is
    there."""
    FileArchive.in_profile_folder(profile_folder).remove_orphan_temporaries()
    with opened_profile(profile_folder) as ports:
        outcome = synchronize_activities(ports)
        fake = ports.source
        assert isinstance(fake, FakeActivitySource)
        assert outcome.completed
        assert outcome.failures == ()
        assert outcome.status_counts[SyncStatus.DOWNLOADED] == COUNT
        assert outcome.status_counts[SyncStatus.PENDING] == 0
        records = ports.index.list_by_start()
        assert len(records) == COUNT
        assert len({r.identity for r in records}) == COUNT  # no duplicate
        assert {r.garmin_activity_id for r in records} == {f.value for f in fake.activities}
        # Only what was not confirmed is downloaded, and only once.
        assert len(fake.downloads) == COUNT - done
        assert all(times == 1 for times in Counter(fake.downloads).values())
        for record in records:
            ports.archive.verify(record.archived)
    assert len(stored_files(profile_folder)) == COUNT  # no orphan file, no duplicate
    assert temporaries(profile_folder) == []
    return outcome


def assert_second_round_changes_nothing(profile_folder: Path) -> None:
    before = tree_digest(profile_folder / "archivio")
    with opened_profile(profile_folder) as ports:
        outcome = synchronize_activities(ports)
        fake = ports.source
        assert isinstance(fake, FakeActivitySource)
        assert (outcome.completed, outcome.new_entries, outcome.downloaded) == (True, 0, 0)
        assert fake.downloads == []
        assert len(ports.index.list_by_start()) == COUNT
    assert tree_digest(profile_folder / "archivio") == before


def tree_digest(folder: Path) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file()
    }


def run_crash_case(tmp_path: Path, point: str) -> int:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    kill_at(profile_folder, point)
    done = check_after_crash(profile_folder)
    resume(profile_folder, done)
    assert_second_round_changes_nothing(profile_folder)
    return done


@pytest.mark.parametrize("point", DEFAULT_POINTS)
def test_t_sync_02_kill_at_a_precise_point_then_resume(tmp_path: Path, point: str) -> None:
    done = run_crash_case(tmp_path, point)

    kind, _, number = point.partition(":")
    if kind in ("list", "cursor"):
        assert done == 0  # nothing is downloaded before the complete list
    elif kind in ("download", "before_add", "after_add", "update"):
        assert done == int(number) - 1  # the in-flight entry is not confirmed, the first ones are


def test_t_sync_02_kill_after_some_entries_at_an_arbitrary_instant_then_resume(
    tmp_path: Path,
) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()

    with SyncWorker("sync", profile_folder, COUNT, PAGE, "none") as worker:
        worker.expect_ready()
        worker.read_until("progress 2")
        worker.kill()  # the process keeps synchronising until it dies

    done = check_after_crash(profile_folder)
    assert done >= 2
    resume(profile_folder, done)
    assert_second_round_changes_nothing(profile_folder)


def test_t_sync_02_a_kill_while_listing_resumes_from_the_saved_cursor(tmp_path: Path) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    kill_at(profile_folder, "list:3")  # two pages confirmed (6 entries), the third does not answer

    with opened_profile(profile_folder) as ports:
        cursor = ports.journal.load_cursor(ListingKind.FULL)
        assert cursor is not None
        assert (cursor.next_offset, cursor.completed) == (2 * PAGE, False)
        assert ports.journal.counts()[SyncStatus.PENDING] == 2 * PAGE
        outcome = synchronize_activities(ports)
        fake = ports.source
        assert isinstance(fake, FakeActivitySource)
        first_page = next(call for call in fake.calls if call.limit is not None)
        assert first_page.offset == 2 * PAGE  # restarts from the cursor, not from the beginning
        assert (outcome.completed, outcome.new_entries, outcome.downloaded) == (True, 4, COUNT)
    assert len(stored_files(profile_folder)) == COUNT


@pytest.mark.collaudo
@pytest.mark.parametrize("point", ALL_POINTS)
def test_t_sync_02_collaudo_kill_at_every_point_of_the_run(tmp_path: Path, point: str) -> None:
    run_crash_case(tmp_path, point)


@pytest.mark.collaudo
@pytest.mark.parametrize("after_entries", range(1, COUNT))
def test_t_sync_02_collaudo_kill_after_n_entries_at_an_arbitrary_instant(
    tmp_path: Path, after_entries: int
) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()

    with SyncWorker("sync", profile_folder, COUNT, PAGE, "none") as worker:
        worker.expect_ready()
        worker.read_until(f"progress {after_entries}")
        worker.kill()

    done = check_after_crash(profile_folder)
    assert done >= after_entries
    resume(profile_folder, done)
    assert_second_round_changes_nothing(profile_folder)


@pytest.mark.collaudo
def test_t_sync_02_collaudo_two_kills_in_a_row_then_resume(tmp_path: Path) -> None:
    profile = create_profile(tmp_path)
    profile_folder = profile.folder
    profile.close()
    first = random.Random(20260701).choice(ALL_POINTS)

    kill_at(profile_folder, first)
    check_after_crash(profile_folder)
    kill_at(profile_folder, "after_add:1")  # always reached: at least one entry is left to download
    done = check_after_crash(profile_folder)
    resume(profile_folder, done)
    assert_second_round_changes_nothing(profile_folder)
