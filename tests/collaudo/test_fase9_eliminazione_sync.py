# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign: what the Garmin synchronization and the profile backup do with
deleted activities (T-SYNC for ADR 018 and T-DATI-05), and the journal entries that Garmin lists
but never serves (`REMOTE_NOT_PROVIDED`).

Real stack (SQLite, on-disk archive, real worker and Garmin adapter); only the `garminconnect`
client is fake, so there is no network. The athlete is invented ("Atleta di prova").
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.domain import (
    FailureReason,
    GarminActivityId,
    RemoteActivity,
    SyncStatus,
)
from ponos_coach.activities.sync import SyncMode
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX
from tests.collaudo import fase8_support as backups
from tests.collaudo.fase2_support import (
    Stack,
    build_stack,
    reopen_stack,
    scripted_client,
)
from tests.collaudo.fase5_support import World
from tests.collaudo.fase9_support import (
    assert_one_table_each,
    delete_use_case,
    identities,
    restore_use_case,
    tombstones,
)
from tests.collaudo.support import open_profile_from_folder, real_ports
from tests.collaudo.test_fase2_sincronizzazione import finished
from tests.fakes.garmin import FIRST_ACTIVITY_ID, make_remote_history
from tests.fixtures.generators.import_files import RUN_SECONDS, fit_run, run_start
from tests.fixtures.generators.zip_writer import garmin_download
from tests.garmin.scripted import ScriptedClient, entry_for, http_error, newest_first

RUNS = 6


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    made = build_stack(tmp_path, scripted_client(RUNS))
    yield made
    made.close()


def downloaded_ids(client: ScriptedClient) -> set[int]:
    return set(client.downloaded)


def client_of(stack: Stack) -> ScriptedClient:
    client = stack.factory.client
    assert isinstance(client, ScriptedClient)
    return client


def test_t_sync_07_a_deleted_garmin_activity_is_not_downloaded_again_by_either_kind_of_round(
    stack: Stack,
) -> None:
    finished(stack.sync())
    ids = identities(stack.profile)
    assert len(ids) == RUNS
    delete_use_case(stack.profile)([ids[0], ids[3]])
    client_of(stack).downloaded.clear()

    verify = finished(stack.sync(SyncMode.VERIFY))
    again = finished(stack.sync())

    assert len(identities(stack.profile)) == RUNS - 2
    assert client_of(stack).downloaded == []
    assert verify.outcome.completed and again.outcome.completed
    assert verify.outcome.failures == () and again.outcome.failures == ()
    assert_one_table_each(stack.profile, RUNS)


def test_t_sync_07_an_activity_imported_from_a_file_then_deleted_is_skipped_by_start_and_duration(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path, scripted_client(3))
    try:
        source = tmp_path / "da-importare"
        source.mkdir()
        (source / "corsa-1.fit").write_bytes(fit_run(1))  # the same activity Garmin will list
        stack.profile.import_folder(source)
        [identity] = identities(stack.profile)
        delete_use_case(stack.profile)([identity])

        result = finished(stack.sync())

        assert result.outcome.skipped_deleted == 1
        assert FIRST_ACTIVITY_ID + 1 not in downloaded_ids(client_of(stack))
        assert result.outcome.failures == ()
        counts = stack.journal_counts()
        assert counts[SyncStatus.SKIPPED] == 1
        assert counts[SyncStatus.PENDING] == counts[SyncStatus.RETRYABLE] == 0
        assert counts[SyncStatus.FAILED] == 0
        assert len(identities(stack.profile)) == 2
        assert_one_table_each(stack.profile, 3)
    finally:
        stack.close()


def test_t_sync_07_the_same_file_under_another_garmin_id_is_recognised_by_its_identity(
    tmp_path: Path,
) -> None:
    history = make_remote_history(3)
    ghost = RemoteActivity(
        GarminActivityId(FIRST_ACTIVITY_ID + 100),
        run_start(1),  # the same run uploaded twice: the list cannot tell it from the deleted one
        float(RUN_SECONDS),
        "running",
        True,
    )
    client = scripted_client(history=history)
    stack = build_stack(tmp_path, client)
    try:
        finished(stack.sync())
        victim = next(r for r in stack.profile.records() if r.start == run_start(1))
        delete_use_case(stack.profile)([victim.identity])
        client._entries.insert(0, entry_for(ghost))
        client.set_download(
            ghost.garmin_id.value, garmin_download(fit_run(1), ghost.garmin_id.value)
        )

        result = finished(stack.sync(SyncMode.VERIFY))

        assert result.outcome.completed and result.outcome.failures == ()
        assert result.outcome.skipped_deleted == 1
        assert len(identities(stack.profile)) == 2
        assert stack.journal_counts()[SyncStatus.SKIPPED] == 1
        assert_one_table_each(stack.profile, 3)
    finally:
        stack.close()


def test_t_sync_07_a_restored_garmin_activity_is_not_duplicated_by_the_next_round(
    stack: Stack,
) -> None:
    finished(stack.sync())
    [victim] = [r for r in stack.profile.records() if r.start == run_start(2)]
    delete_use_case(stack.profile)([victim.identity])
    restore_use_case(stack.profile)([victim.identity])
    client_of(stack).downloaded.clear()

    result = finished(stack.sync(SyncMode.VERIFY))

    assert len(identities(stack.profile)) == RUNS
    assert client_of(stack).downloaded == []
    assert result.outcome.failures == ()
    [again] = [r for r in stack.profile.records() if r.start == run_start(2)]
    assert (again.origin, again.garmin_activity_id, again.imported_at) == (
        victim.origin,
        victim.garmin_activity_id,
        victim.imported_at,
    )


# --- Entries that Garmin lists but never serves -------------------------------------------------


class MutedClient(ScriptedClient):
    """Lists everything but answers 503 to the download of the `muted` ids (an answer-less hole,
    while the list and the other downloads answer)."""

    muted: set[int]

    def download_original(self, activity_id: int) -> bytes:
        if activity_id in self.muted:
            self.downloaded.append(activity_id)
            raise http_error(503)
        return super().download_original(activity_id)


def muted_client(count: int, muted: set[int]) -> MutedClient:
    history = make_remote_history(count)
    from tests.fakes.garmin import original_zip  # noqa: PLC0415

    client = MutedClient(
        [entry_for(a) for a in newest_first(history)],
        downloads={
            a.garmin_id.value: original_zip(a.garmin_id.value - FIRST_ACTIVITY_ID) for a in history
        },
    )
    client.muted = muted
    return client


def test_t_sync_08_entries_garmin_never_serves_fail_as_not_provided_after_three_silent_rounds(
    tmp_path: Path,
) -> None:
    muted = {FIRST_ACTIVITY_ID + n for n in (0, 1, 2)}  # the oldest runs
    client = muted_client(8, muted)
    stack = build_stack(tmp_path, client)
    try:
        rounds = [finished(stack.sync()) for _ in range(12)]

        assert rounds[-1].outcome.completed
        counts = stack.journal_counts()
        assert counts[SyncStatus.FAILED] == 3
        assert counts[SyncStatus.DOWNLOADED] == 5
        assert counts[SyncStatus.PENDING] == counts[SyncStatus.RETRYABLE] == 0
        reasons = {f.reason for r in rounds for f in r.outcome.failures}
        assert FailureReason.REMOTE_NOT_PROVIDED in reasons
        assert len(identities(stack.profile)) == 5
        before = len(client.downloaded)
        finished(stack.sync())  # a failed entry is not asked again
        assert len(client.downloaded) == before
    finally:
        stack.close()


def test_t_sync_08_a_round_never_gets_stuck_on_silent_entries_and_the_full_check_rearms_them(
    tmp_path: Path,
) -> None:
    muted = {FIRST_ACTIVITY_ID + n for n in (0, 1)}
    client = muted_client(6, muted)
    stack = build_stack(tmp_path, client)
    try:
        for _ in range(12):
            finished(stack.sync())
        assert stack.journal_counts()[SyncStatus.FAILED] == 2
        client.muted.clear()  # Garmin serves them now
        finished(stack.sync())
        assert len(identities(stack.profile)) == 4  # a normal round leaves them alone

        full = finished(stack.sync(SyncMode.VERIFY))
        for _ in range(3):
            finished(stack.sync())

        assert full.outcome.completed
        assert len(identities(stack.profile)) == 6
        assert stack.journal_counts()[SyncStatus.DOWNLOADED] == 6
        assert stack.journal_counts()[SyncStatus.FAILED] == 0
    finally:
        stack.close()


def test_t_sync_08_a_404_is_a_final_rejection_that_the_full_check_does_not_rearm(
    tmp_path: Path,
) -> None:
    client = scripted_client(4)
    del client._downloads[FIRST_ACTIVITY_ID]
    stack = build_stack(tmp_path, client)
    try:
        first = finished(stack.sync())
        finished(stack.sync(SyncMode.VERIFY))
        finished(stack.sync())

        assert [f.reason for f in first.outcome.failures] == [FailureReason.REMOTE_REJECTED]
        assert stack.journal_counts()[SyncStatus.FAILED] == 1
        assert len(identities(stack.profile)) == 3
    finally:
        stack.close()


# --- Backup and restore of the profile keep the deletions -------------------------------------


def test_t_dati_05_the_backup_keeps_the_deleted_activities_and_they_can_be_brought_back(
    tmp_path: Path,
) -> None:
    world = backups.make_rich_world(tmp_path / "p", runs=6)
    try:
        ids = identities(world.profile)
        victims = [ids[1], ids[4]]
        delete_use_case(world.profile)(victims)
        backup = tmp_path / f"copy{BACKUP_SUFFIX}"
        backups.backup_use_case()(world.profile.folder, backup)
        target = tmp_path / "again" / "profilo"
        backups.restore_use_case()(backup, target)
        source_tables = backups.all_tables(world.profile.folder / backups.database_name())
        restored_tables = backups.all_tables(target / backups.database_name())
        assert restored_tables == source_tables
        assert len(restored_tables["deleted_activity"]) == 2

        profile = open_profile_from_folder(real_ports(tmp_path / "again" / "config"), target)
        try:
            copy = World(profile, tmp_path / "again", seed=False)
            assert {t.identity for t in tombstones(profile)} == set(victims)
            assert identities(profile) == [i for i in ids if i not in victims]
            assert_one_table_each(profile, 6)
            summary = restore_use_case(profile)(victims)
            assert set(summary.restored) == set(victims)
            assert identities(profile) == ids
            assert (target / PROFILE_ARCHIVE_DIR).is_dir()
            del copy
        finally:
            profile.close()
    finally:
        world.close()


def test_t_dati_05_a_synchronization_on_a_restored_profile_does_not_bring_deleted_activities_back(
    tmp_path: Path,
) -> None:
    stack = build_stack(tmp_path / "a", scripted_client(RUNS))
    finished(stack.sync())
    ids = identities(stack.profile)
    delete_use_case(stack.profile)(ids[:2])
    backup = tmp_path / f"copy{BACKUP_SUFFIX}"
    backups.backup_use_case()(stack.folder, backup)
    stack.close()
    target = tmp_path / "b" / "profilo"
    backups.restore_use_case()(backup, target)
    client = scripted_client(RUNS)
    again = reopen_stack(tmp_path / "b", target, client, linked=True)
    try:
        # a restored profile has no Garmin link (the token lives outside): the round is a fresh one
        result = finished(again.sync(SyncMode.VERIFY))

        assert result.outcome.failures == ()
        assert len(identities(again.profile)) == RUNS - 2
        assert len(tombstones(again.profile)) == 2
        assert_one_table_each(again.profile, RUNS)
    finally:
        again.close()
