# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sync: an entry that Garmin never serves must not block every round (PLAN.md, phase 8).

Real case: old activities whose download always answers 5xx while the list and the other
downloads answer. They used to stay pending forever and stopped each round after three entries.
"""

from __future__ import annotations

from ponos_coach.activities.domain import FailureReason, SyncStatus
from ponos_coach.activities.ports import ServiceUnavailableError
from tests.fixtures.generators.sync_harness import SyncRig, in_memory_rig, remote_id

BAD = range(5)  # the oldest runs
GOOD = range(5, 8)


def rig_with_stuck_entries() -> SyncRig:
    rig = in_memory_rig(8)
    for number in BAD:
        rig.fake.fail_download_of(remote_id(number), ServiceUnavailableError("no answer"))
    return rig


def test_entries_that_garmin_never_serves_stop_blocking_the_rounds() -> None:
    rig = rig_with_stuck_entries()

    outcomes = [rig.run() for _ in range(12)]

    assert outcomes[-1].completed
    assert {rig.status_of(n) for n in BAD} == {SyncStatus.FAILED}
    assert {rig.status_of(n) for n in GOOD} == {SyncStatus.DOWNLOADED}
    entry = rig.journal.get(remote_id(0))
    assert entry is not None
    assert entry.failure is FailureReason.REMOTE_NOT_PROVIDED
    assert rig.journal.counts()[SyncStatus.RETRYABLE] == 0
