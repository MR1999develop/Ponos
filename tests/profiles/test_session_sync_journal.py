# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The open profile also exposes the sync journal."""

from __future__ import annotations

from datetime import UTC, datetime

from ponos_coach.activities.domain import SyncStatus
from tests.builders import remote_activity
from tests.profiles.test_use_cases import World, request

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=UTC)


def test_a_new_profile_has_an_empty_journal() -> None:
    world = World()
    session = world.create()(request())

    counts = session.sync_journal.counts()

    assert all(counts[status] == 0 for status in SyncStatus)
    assert session.sync_journal.last_completed_at() is None


def test_the_journal_of_the_session_is_the_one_of_the_open_profile() -> None:
    world = World()
    session = world.create()(request())

    session.sync_journal.add_pending([remote_activity(1), remote_activity(2)], NOW)
    session.close()
    reopened = world.storage_class(world.disk).open(session.folder)

    assert reopened.sync_journal.counts()[SyncStatus.PENDING] == 2
