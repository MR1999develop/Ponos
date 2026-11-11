# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign, deleting and restoring activities (ADR 018, schema 19): T-DATI.

Real profile (SQLite, on-disk archive, real FIT decoder); the load, the PMC and the plan are
recomputed through the real use cases and compared with a profile that never had the activity
(the naive oracle). Hostile cases: files gone or altered, identities that are not there, imports
that forget the tombstones.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.deletion import RestoreStatus
from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.activities.use_cases import (
    DeletedHandling,
    DeletedPolicy,
    ImportStatus,
    import_activities,
)
from ponos_coach.goals.domain import Priority
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from tests.activities.import_fakes import FixedClock
from tests.collaudo.fase5_support import TODAY, World, make_world, race
from tests.collaudo.fase9_support import (
    archive_snapshot,
    archived_file_of,
    assert_one_table_each,
    delete_use_case,
    identities,
    import_runs,
    restore_use_case,
    same_pmc,
    tombstones,
)
from tests.collaudo.support import NOW
from tests.fixtures.generators.import_files import fit_run

RUNS = 10


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    made = make_world(tmp_path)
    yield made
    made.close()


@pytest.fixture
def new_world(tmp_path: Path) -> Iterator[Callable[[str], World]]:
    """Makes more worlds under `tmp_path`, all closed at the end even if the test fails."""
    made: list[World] = []

    def make(name: str) -> World:
        made.append(make_world(tmp_path / name))
        return made[-1]

    yield make
    for each in made:
        each.close()


# --- Load, PMC and plan are recomputed without the deleted activity -------------------------------


def test_t_dati_07_load_and_pmc_without_the_deleted_activities_equal_a_profile_that_never_had_them(
    new_world: Callable[[str], World],
) -> None:
    full = new_world("full")
    ids = import_runs(full, RUNS)
    before = full.overview()
    kept = new_world("kept")
    for n in range(RUNS):
        if n not in (2, 3, 7):
            kept.import_run(TODAY - timedelta(days=60 - n), bpm=140 + n % 20)

    delete_use_case(full.profile)([ids[2], ids[3], ids[7]])

    after = full.overview()
    naive = kept.overview()
    assert [a.start for a in after.activities] == [a.start for a in naive.activities]
    assert [a.load for a in after.activities] == [a.load for a in naive.activities]
    assert same_pmc(after.pmc, naive.pmc)
    assert not same_pmc(after.pmc, before.pmc)
    assert len(after.activities) == RUNS - 3


def test_t_dati_07_restoring_gives_back_exactly_the_load_and_the_pmc_of_before(
    world: World,
) -> None:
    ids = import_runs(world, RUNS)
    before = world.overview()
    delete_use_case(world.profile)(ids[1::2])
    assert not same_pmc(world.overview().pmc, before.pmc)

    summary = restore_use_case(world.profile)(ids[1::2])

    assert set(summary.restored) == set(ids[1::2])
    again = world.overview()
    assert again.activities == before.activities and same_pmc(again.pmc, before.pmc)
    assert not tombstones(world.profile)
    assert_one_table_each(world.profile, RUNS)


def test_t_dati_07_the_plan_without_the_deleted_runs_is_the_plan_of_a_profile_without_them_and_back(
    new_world: Callable[[str], World],
) -> None:
    def build(name: str, skip: frozenset[int]) -> World:
        w = new_world(name)
        for n in range(RUNS):
            if n not in skip:
                w.import_run(TODAY - timedelta(days=30 - 2 * n), bpm=150)
        w.add_goal(race("a", TODAY + timedelta(days=84), Priority.A, 10_000))
        return w

    full = build("full", frozenset())
    naive = build("naive", frozenset({0, 1, 2}))
    full.built()
    original = full.saved_plan()
    naive.built()
    ids = identities(full.profile)

    delete_use_case(full.profile)(ids[:3])
    full.built()

    assert full.saved_plan() == naive.saved_plan()
    restore_use_case(full.profile)(ids[:3])
    full.built()
    assert full.saved_plan() == original
    full.built()
    assert full.saved_plan() == original  # deterministic: same data, same plan


# --- Idempotence and hostile arguments ------------------------------------------------------------


def test_deleting_twice_unknown_or_repeated_identities_changes_nothing_more(
    world: World, new_world: Callable[[str], World]
) -> None:
    ids = import_runs(world, 4)
    stranger = new_world("other")
    foreign = stranger.import_run(TODAY - timedelta(days=5)).identity
    snapshot = archive_snapshot(world.profile.folder)

    first = delete_use_case(world.profile)([ids[0], ids[0], foreign, ids[0]])
    second = delete_use_case(world.profile)([ids[0]])
    nothing = delete_use_case(world.profile)([])

    assert [t.identity for t in first] == [ids[0]]
    assert second == () and nothing == ()
    assert_one_table_each(world.profile, 4)
    assert archive_snapshot(world.profile.folder) == snapshot  # the file stays in the archive
    summary = restore_use_case(world.profile)([ids[1], foreign, ids[0], ids[0]])
    assert summary.restored == (ids[0],)  # not deleted / unknown: not in the result
    assert restore_use_case(world.profile)([ids[0]]).results == ()
    assert_one_table_each(world.profile, 4)


def test_deleting_every_activity_leaves_an_empty_overview_and_restoring_all_brings_them_back(
    world: World,
) -> None:
    ids = import_runs(world, 5)
    before = world.overview()

    delete_use_case(world.profile)(ids)

    empty = world.overview()
    assert empty.activities == () and empty.excluded == ()
    assert len(empty.pmc) == 0
    restore_use_case(world.profile)(ids)
    again = world.overview()
    assert again.activities == before.activities and same_pmc(again.pmc, before.pmc)


# --- Restore with damaged archive files -----------------------------------------------------------


def test_restore_with_the_file_missing_altered_or_swapped_says_why_and_keeps_the_tombstone(
    world: World,
) -> None:
    ids = import_runs(world, 4)
    delete_use_case(world.profile)(ids)
    missing, flipped, swapped, fine = ids
    by_id = {t.identity: t for t in tombstones(world.profile)}
    path = {i: archived_file_of(world.profile, by_id[i]) for i in ids}
    original = {i: path[i].read_bytes() for i in ids}
    path[missing].unlink()
    damaged = bytearray(original[flipped])
    damaged[len(damaged) // 2] ^= 0xFF
    path[flipped].write_bytes(bytes(damaged))
    path[swapped].write_bytes(original[fine])  # a valid FIT, but another activity

    summary = restore_use_case(world.profile)(ids)

    status = {r.identity: r.status for r in summary.results}
    assert status[missing] is RestoreStatus.FILE_MISSING
    assert status[flipped] is RestoreStatus.FILE_DAMAGED
    assert status[swapped] is RestoreStatus.FILE_DAMAGED
    assert status[fine] is RestoreStatus.RESTORED
    assert identities(world.profile) == [fine]
    assert {t.identity for t in tombstones(world.profile)} == {missing, flipped, swapped}
    assert_one_table_each(world.profile, 4)
    path[swapped].write_bytes(original[swapped])  # the user puts the files back
    path[flipped].write_bytes(original[flipped])
    retry = restore_use_case(world.profile)([flipped, swapped])
    assert set(retry.restored) == {flipped, swapped}
    assert_one_table_each(world.profile, 4)


# --- Imports --------------------------------------------------------------------------------------


def deleted_world(world: World, tmp_path: Path) -> tuple[Path, ActivityIdentity, ActivityIdentity]:
    source = tmp_path / "da-importare"
    source.mkdir()
    for n in range(3):
        (source / f"corsa-{n}.fit").write_bytes(fit_run(n))
    summary = world.profile.import_folder(source)
    assert summary.count(ImportStatus.IMPORTED) == 3
    first, _, last = identities(world.profile)
    delete_use_case(world.profile)([first, last])
    return source, first, last


def test_a_folder_import_leaves_the_deleted_activities_out_and_files_chosen_by_hand_bring_them_back(
    world: World, tmp_path: Path
) -> None:
    source, first, last = deleted_world(world, tmp_path)
    store = world.profile.session.deleted_activities
    archive, index = world.profile.archive, world.profile.session.activities

    folder = import_activities(
        FolderImportSource(source),
        archive,
        index,
        FixedClock(NOW),
        deleted=DeletedHandling(store, DeletedPolicy.LEAVE_OUT),
    )

    assert folder.count(ImportStatus.LEFT_OUT) == 2
    assert folder.count(ImportStatus.ALREADY_PRESENT) == 1
    assert len(identities(world.profile)) == 1
    chosen = import_activities(
        FilesImportSource([source / "corsa-0.fit"]),
        archive,
        index,
        FixedClock(NOW),
        deleted=DeletedHandling(store, DeletedPolicy.BRING_BACK),
    )
    assert chosen.count(ImportStatus.BROUGHT_BACK) == 1
    assert len(identities(world.profile)) == 2
    assert {t.identity for t in tombstones(world.profile)} <= {first, last}
    assert_one_table_each(world.profile, 3)


def test_an_import_path_that_forgets_the_tombstones_fails_loudly_and_resurrects_nothing(
    world: World, tmp_path: Path
) -> None:
    source, _first, _last = deleted_world(world, tmp_path)
    snapshot = archive_snapshot(world.profile.folder)

    summary = world.profile.import_folder(source)  # no `deleted=`: like a path that forgot

    assert summary.count(ImportStatus.FAILED) == 2
    assert len(identities(world.profile)) == 1
    assert len(tombstones(world.profile)) == 2
    assert archive_snapshot(world.profile.folder) == snapshot
    assert_one_table_each(world.profile, 3)
