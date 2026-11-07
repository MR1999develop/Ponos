# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SYNC-01 on the real database: a history of 5,000 activities, full then incremental.

The index and journal are the SQLite ones (in-memory transactions copy everything at each
transaction, too much for this volume); Garmin is fake, the archive is in memory and the "files"
downloaded are tiny (`run:<n>`) with fast decoding: the sync is tested, not
the FIT reader. Clock and wait do not sleep.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.domain import (
    ListingCursor,
    ListingKind,
    RemoteActivity,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.sync import SyncMode
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.garmin import Call
from tests.fixtures.generators.import_files import RUN_SECONDS, run_start
from tests.fixtures.generators.sync_harness import (
    FakeSyncClock,
    SyncRig,
    fast_decode,
    make_source,
    passthrough_extract,
    remote_id,
    run_file,
)

HISTORY = 5000
PAGE_SIZE = 100


@pytest.fixture
def rig(tmp_path: Path) -> Iterator[SyncRig]:
    storage = SqliteProfileStorage()
    profile = storage.create(
        tmp_path,
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "UTC",
    )
    clock = FakeSyncClock()
    fake = make_source(HISTORY, clock=clock)
    for number in range(HISTORY):
        fake.set_original(remote_id(number), run_file(number))
    memory = InMemoryActivityArchive()
    try:
        yield SyncRig(
            fake,
            fake,
            profile.activities,
            profile.sync_journal,
            memory,
            memory,
            clock,
            SyncPolicy(page_size=PAGE_SIZE),
            extract=passthrough_extract,
            decode=fast_decode,
        )
    finally:
        profile.close()


def test_t_sync_01_five_thousand_activities_full_then_incremental(rig: SyncRig) -> None:
    full = rig.run()

    assert full.kind is ListingKind.FULL
    assert full.completed
    assert (full.new_entries, full.downloaded, full.matched) == (HISTORY, HISTORY, 0)
    assert full.status_counts[SyncStatus.DOWNLOADED] == HISTORY
    # an empty page closes the listing: the declared total does not stop it (D-F2-04)
    assert rig.fake.call_count(Call.LIST_PAGE) == HISTORY // PAGE_SIZE + 1
    assert rig.fake.call_count(Call.DOWNLOAD) == HISTORY
    assert len(set(rig.fake.downloads)) == HISTORY  # no repeated download
    assert rig.journal.load_cursor(ListingKind.FULL) == ListingCursor(HISTORY, HISTORY, True)
    assert len(rig.records()) == HISTORY
    assert len(rig.memory.files) == HISTORY

    for number in range(HISTORY, HISTORY + 7):  # few new ones
        rig.fake.add_activity(
            RemoteActivity(remote_id(number), run_start(number), float(RUN_SECONDS), "running")
        )
        rig.fake.set_original(remote_id(number), run_file(number))
    lists_before = rig.fake.call_count(Call.LIST_PAGE)
    downloads_before = rig.fake.call_count(Call.DOWNLOAD)

    incremental = rig.run()

    assert incremental.kind is ListingKind.INCREMENTAL
    assert (incremental.new_entries, incremental.downloaded) == (7, 7)
    assert rig.fake.call_count(Call.LIST_PAGE) - lists_before == 2  # the second page is known
    assert rig.fake.call_count(Call.DOWNLOAD) - downloads_before == 7
    assert len(rig.records()) == HISTORY + 7

    verify = rig.run(SyncMode.VERIFY)  # lists everything again, without downloading anything
    assert verify.kind is ListingKind.VERIFY
    assert (verify.new_entries, verify.downloaded) == (0, 0)
    assert rig.fake.call_count(Call.DOWNLOAD) - downloads_before == 7
