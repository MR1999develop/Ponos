# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Delete and restore activities, and what the synchronization and the imports do with them.

The acceptance test comes first: an activity the user deleted does not come back with the next
synchronization, and the synchronization neither fails on it nor leaves it "to download". Fakes in
memory; the same rules on SQLite are in `tests/storage` and `tests/contracts`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.deletion import (
    DeleteActivities,
    RestoreActivities,
    RestoreStatus,
)
from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityOrigin,
    DeletedActivity,
    SyncStatus,
)
from ponos_coach.activities.ports import ActivityIndexError
from ponos_coach.activities.sync import SyncMode
from ponos_coach.activities.use_cases import (
    DeletedHandling,
    DeletedPolicy,
    ImportStatus,
    import_activities,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.activities.import_fakes import FixedClock, InMemoryImportSource
from tests.builders import IMPORTED_AT, fit_identity, make_deleted, make_record
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.garmin import FIRST_ACTIVITY_ID
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fixtures.generators.import_files import fit_run, run_start
from tests.fixtures.generators.sync_harness import SyncRig, in_memory_rig, remote_id

DELETED_AT = IMPORTED_AT + timedelta(days=1)


def import_files(rig: SyncRig, files: dict[str, bytes]) -> None:
    handling = DeletedHandling(rig.deleted, DeletedPolicy.LEAVE_OUT)
    import_activities(
        InMemoryImportSource(files), rig.archive, rig.index, FixedClock(), deleted=handling
    )


def delete(rig: SyncRig, *numbers: int) -> tuple[DeletedActivity, ...]:
    identities = [fit_identity_of(rig, n) for n in numbers]
    return DeleteActivities(rig.index, rig.deleted, FixedClock(DELETED_AT))(identities)


def fit_identity_of(rig: SyncRig, number: int) -> ActivityIdentity:
    """The identity of run `number` as the index has it (the start of the run finds it)."""
    start = run_start(number)
    [record] = [r for r in rig.index.list_by_start() if r.start == start]
    return record.identity


# --- Acceptance: a deleted activity does not come back with the synchronization ------------


def test_a_deleted_activity_does_not_come_back_with_the_next_synchronization() -> None:
    rig = in_memory_rig(3)
    rig.run()
    delete(rig, 1)
    rig.fake.downloads.clear()

    outcome = rig.run(SyncMode.VERIFY)

    assert sorted(r.start for r in rig.records()) == [run_start(0), run_start(2)]
    assert rig.fake.downloads == []
    assert outcome.failures == ()
    assert outcome.completed


def test_an_activity_imported_by_hand_and_then_deleted_is_skipped_not_downloaded() -> None:
    rig = in_memory_rig(4)
    import_files(rig, {f"corsa-{n}.fit": fit_run(n) for n in range(2)})
    delete(rig, 1)

    outcome = rig.run()

    assert outcome.skipped_deleted == 1
    assert (outcome.matched, outcome.downloaded) == (1, 2)
    assert remote_id(1) not in rig.fake.downloads
    assert rig.status_of(1) is SyncStatus.SKIPPED
    assert sorted(r.start for r in rig.records()) == [
        run_start(0),
        run_start(2),
        run_start(3),
    ]
    assert outcome.failures == ()
    counts = outcome.status_counts
    assert counts[SyncStatus.PENDING] == counts[SyncStatus.RETRYABLE] == 0
    assert counts[SyncStatus.FAILED] == 0


def test_a_skipped_entry_is_final_and_the_next_synchronization_leaves_it_alone() -> None:
    rig = in_memory_rig(2)
    import_files(rig, {"corsa-0.fit": fit_run(0)})
    delete(rig, 0)
    rig.run()
    rig.fake.calls.clear()

    again = rig.run(SyncMode.VERIFY)

    assert again.skipped_deleted == 0
    assert rig.status_of(0) is SyncStatus.SKIPPED
    assert rig.fake.downloads == [remote_id(1)]  # only from the first run


def test_a_tombstone_with_the_garmin_id_stops_the_download_whatever_the_times() -> None:
    rig = in_memory_rig(2)
    record = make_record(
        fit_identity(99),
        start=run_start(1) + timedelta(hours=5),
        origin=ActivityOrigin.GARMIN,
        garmin_activity_id=remote_id(1).value,
    )
    rig.deleted.add(DeletedActivity.of(record, DELETED_AT))

    outcome = rig.run()

    assert outcome.skipped_deleted == 1
    assert rig.fake.downloads == [remote_id(0)]
    assert rig.status_of(1) is SyncStatus.SKIPPED


def test_a_tombstone_with_another_garmin_id_does_not_stop_the_download() -> None:
    rig = in_memory_rig(1)
    record = make_record(
        fit_identity(99),
        start=run_start(0),
        elapsed_s=1800.0,
        origin=ActivityOrigin.GARMIN,
        garmin_activity_id=FIRST_ACTIVITY_ID + 500,
    )
    rig.deleted.add(DeletedActivity.of(record, DELETED_AT))

    outcome = rig.run()

    assert (outcome.skipped_deleted, outcome.downloaded) == (0, 1)


def test_a_downloaded_file_that_is_a_deleted_activity_is_not_recorded() -> None:
    """The list could not tell (the times differ), but the file's identity does."""
    rig = in_memory_rig(1)
    import_files(rig, {"corsa-0.fit": fit_run(0)})
    [tombstone] = delete(rig, 0)
    far = replace(tombstone, start=tombstone.start + timedelta(hours=3))
    rig.deleted.remove(tombstone.identity)
    rig.deleted.add(far)

    outcome = rig.run()

    assert outcome.skipped_deleted == 1
    assert rig.fake.downloads == [remote_id(0)]
    assert rig.records() == []
    assert rig.status_of(0) is SyncStatus.SKIPPED
    assert outcome.failures == ()


def test_a_restored_activity_is_not_downloaded_or_duplicated_by_the_next_synchronization() -> None:
    rig = in_memory_rig(2)
    rig.run()
    [tombstone] = delete(rig, 0)
    restored = RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)(
        [tombstone.identity]
    )
    rig.fake.downloads.clear()

    outcome = rig.run(SyncMode.VERIFY)

    assert restored.restored == (tombstone.identity,)
    assert len(rig.records()) == 2
    assert rig.fake.downloads == []
    assert outcome.failures == ()


# --- Delete ---------------------------------------------------------------------------------


def fresh_profile() -> OpenProfile:
    return InMemoryProfileStorage().create(
        Path("profilo-di-prova"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "UTC",
    )


def test_deleting_moves_the_activity_to_the_tombstones_and_keeps_the_others() -> None:
    profile = fresh_profile()
    first, second = make_record(fit_identity(1)), make_record(fit_identity(2))
    profile.activities.add(first)
    profile.activities.add(second)

    result = DeleteActivities(
        profile.activities, profile.deleted_activities, FixedClock(DELETED_AT)
    )([first.identity])

    assert result == (DeletedActivity.of(first, DELETED_AT),)
    assert profile.activities.list_by_start() == [second]
    assert profile.deleted_activities.get(first.identity) == result[0]


def test_deleting_an_activity_that_is_not_there_is_left_out() -> None:
    profile = fresh_profile()
    record = make_record(fit_identity(1))
    profile.activities.add(record)
    delete_activities = DeleteActivities(
        profile.activities, profile.deleted_activities, FixedClock(DELETED_AT)
    )
    delete_activities([record.identity])

    assert delete_activities([record.identity, fit_identity(7)]) == ()


def test_deleting_does_not_touch_the_archive() -> None:
    rig = in_memory_rig(1)
    rig.run()
    files_before = dict(rig.memory.files)

    delete(rig, 0)

    assert rig.memory.files == files_before


def test_a_failure_in_the_middle_deletes_nothing() -> None:
    profile = fresh_profile()
    first, second = make_record(fit_identity(1)), make_record(fit_identity(2))
    profile.activities.add(first)
    profile.activities.add(second)
    profile.deleted_activities.add(make_deleted(make_record(fit_identity(3))))
    # A tombstone that is already there makes the second `add` fail: the first must roll back.
    clash = make_record(fit_identity(2), start=second.start)

    with pytest.raises(ActivityIndexError), profile.activities.transaction():
        profile.activities.remove(first.identity)
        profile.deleted_activities.add(DeletedActivity.of(first, DELETED_AT))
        profile.deleted_activities.add(DeletedActivity.of(clash, DELETED_AT))  # still indexed

    assert sorted(r.identity.file_id.serial_number for r in profile.activities.list_by_start()) == [
        1,
        2,
    ]
    assert profile.deleted_activities.get(first.identity) is None


# --- Restore --------------------------------------------------------------------------------


def test_restoring_gives_back_exactly_the_record_that_was_deleted() -> None:
    rig = in_memory_rig(3)
    rig.run()
    before = rig.records()
    tombstones = delete(rig, 0, 2)

    summary = RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)(
        [t.identity for t in tombstones]
    )

    assert [r.status for r in summary.results] == [RestoreStatus.RESTORED] * 2
    assert rig.records() == before  # origin, import instant and Garmin id included
    assert rig.deleted.list_latest_first() == []


def test_restoring_an_identity_that_is_not_deleted_is_left_out() -> None:
    rig = in_memory_rig(1)
    rig.run()

    summary = RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)([fit_identity(5)])

    assert summary.results == ()


def test_restoring_when_the_file_is_gone_leaves_it_deleted_and_says_why() -> None:
    rig = in_memory_rig(2)
    rig.run()
    first, second = delete(rig, 0, 1)
    del rig.memory.files[first.archived.relative_path]

    summary = RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)(
        [first.identity, second.identity]
    )

    assert [(r.identity, r.status) for r in summary.results] == [
        (first.identity, RestoreStatus.FILE_MISSING),
        (second.identity, RestoreStatus.RESTORED),
    ]
    assert rig.deleted.get(first.identity) == first
    assert rig.index.get(first.identity) is None


def test_restoring_when_the_file_was_altered_leaves_it_deleted() -> None:
    rig = in_memory_rig(1)
    rig.run()
    [tombstone] = delete(rig, 0)
    data = bytearray(rig.memory.files[tombstone.archived.relative_path])
    data[20] ^= 0xFF
    rig.memory.files[tombstone.archived.relative_path] = bytes(data)

    summary = RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)(
        [tombstone.identity]
    )

    assert summary.results[0].status is RestoreStatus.FILE_DAMAGED
    assert rig.deleted.get(tombstone.identity) == tombstone


@settings(max_examples=15, deadline=None)
@given(st.sets(st.integers(min_value=0, max_value=4)))
def test_deleting_any_subset_and_restoring_it_changes_nothing(numbers: set[int]) -> None:
    rig = in_memory_rig(5)
    rig.run()
    before = rig.records()

    tombstones = delete(rig, *sorted(numbers))
    assert len(rig.records()) == 5 - len(numbers)
    RestoreActivities(rig.index, rig.deleted, rig.archive, rig.decode)(
        [t.identity for t in tombstones]
    )

    assert rig.records() == before
    assert rig.deleted.list_latest_first() == []


# --- Imports --------------------------------------------------------------------------------


def deleted_profile_with_a_run() -> tuple[
    OpenProfile, DeletedActivity, bytes, InMemoryActivityArchive
]:
    profile = fresh_profile()
    archive = InMemoryActivityArchive()
    data = fit_run(1)
    import_activities(
        InMemoryImportSource({"a.fit": data}),
        archive,
        profile.activities,
        FixedClock(),
        deleted=DeletedHandling(profile.deleted_activities, DeletedPolicy.LEAVE_OUT),
    )
    [record] = profile.activities.list_by_start()
    [tombstone] = DeleteActivities(profile.activities, profile.deleted_activities, FixedClock())(
        [record.identity]
    )
    return profile, tombstone, data, archive


def test_an_import_of_a_folder_leaves_a_deleted_activity_out_and_says_so() -> None:
    profile, tombstone, data, archive = deleted_profile_with_a_run()

    summary = import_activities(
        InMemoryImportSource({"a.fit": data}),
        archive,
        profile.activities,
        FixedClock(),
        deleted=DeletedHandling(profile.deleted_activities, DeletedPolicy.LEAVE_OUT),
    )

    [result] = summary.results
    assert (result.status, result.matches) == (ImportStatus.LEFT_OUT, tombstone.identity)
    assert profile.activities.list_by_start() == []
    assert profile.deleted_activities.get(tombstone.identity) == tombstone


def test_an_import_of_chosen_files_brings_the_activity_back_as_it_was() -> None:
    profile, tombstone, data, archive = deleted_profile_with_a_run()

    summary = import_activities(
        InMemoryImportSource({"a.fit": data}),
        archive,
        profile.activities,
        FixedClock(IMPORTED_AT + timedelta(days=9)),
        deleted=DeletedHandling(profile.deleted_activities, DeletedPolicy.BRING_BACK),
    )

    [result] = summary.results
    assert result.status is ImportStatus.BROUGHT_BACK
    [record] = profile.activities.list_by_start()
    assert record.identity == tombstone.identity
    assert record.imported_at == tombstone.imported_at  # as before the deletion
    assert profile.deleted_activities.list_latest_first() == []
    assert summary.count(ImportStatus.BROUGHT_BACK) == 1


def test_an_import_without_the_tombstones_cannot_bring_a_deleted_activity_back() -> None:
    """Whoever forgets to pass the tombstones gets a failed file, not a resurrected activity."""
    profile, tombstone, data, archive = deleted_profile_with_a_run()

    summary = import_activities(
        InMemoryImportSource({"a.fit": data}),
        archive,
        profile.activities,
        FixedClock(),
    )

    assert [r.status for r in summary.results] == [ImportStatus.FAILED]
    assert profile.activities.list_by_start() == []
    assert profile.deleted_activities.get(tombstone.identity) == tombstone


def test_a_file_that_was_never_deleted_is_imported_as_before() -> None:
    profile = fresh_profile()

    summary = import_activities(
        InMemoryImportSource({"a.fit": fit_run(1)}),
        InMemoryActivityArchive(),
        profile.activities,
        FixedClock(),
        deleted=DeletedHandling(profile.deleted_activities, DeletedPolicy.BRING_BACK),
    )

    assert [r.status for r in summary.results] == [ImportStatus.IMPORTED]
