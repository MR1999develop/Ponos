# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign: a real kill halfway through deleting or restoring activities
(T-DATI-02/04 for ADR 018).

A subprocess opens the profile, takes the lock and deletes (or restores) activities with the real
SQLite stores; it stops at a precise point and the test kills it with no cleanup. Oracles after the
restart: the lock is free; every identity is in exactly one of the two tables (never both, never
neither); the state is the one before the operation or the one after it, never a mix; the archive
is byte for byte the same; repeating the operation completes it with no duplicate.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.domain import ActivityIdentity
from tests.collaudo.fase5_support import World, make_world
from tests.collaudo.fase9_support import (
    archive_snapshot,
    assert_one_table_each,
    delete_use_case,
    identities,
    import_runs,
    keys,
    restore_use_case,
    same_pmc,
    tombstones,
)
from tests.collaudo.processes import Worker
from tests.collaudo.support import open_profile_from_folder, real_ports

RUNS = 5
TARGETS = (1, 3)


@pytest.fixture
def profile_at_rest(tmp_path: Path) -> Iterator[tuple[Path, list[ActivityIdentity]]]:
    """A closed profile with RUNS imported runs; yields its folder and the identities."""
    world = make_world(tmp_path)
    ids = import_runs(world, RUNS)
    folder = world.profile.folder
    world.close()
    yield folder, ids


def reopen(tmp_path: Path, folder: Path) -> World:
    return World(
        open_profile_from_folder(real_ports(tmp_path / "config"), folder), tmp_path, seed=False
    )


def kill_during(command: str, folder: Path, point: str, victims: list[ActivityIdentity]) -> None:
    with Worker(command, folder, point, *keys(victims)) as worker:
        worker.expect_ready()
        worker.read_until("blocked")
        worker.kill()


@pytest.mark.parametrize("point", ["after_remove", "before_commit"])
def test_t_dati_04_a_kill_before_the_commit_of_a_deletion_leaves_everything_as_it_was(
    tmp_path: Path, profile_at_rest: tuple[Path, list[ActivityIdentity]], point: str
) -> None:
    folder, ids = profile_at_rest
    snapshot = archive_snapshot(folder)
    victims = [ids[i] for i in TARGETS]

    kill_during("delete", folder, point, victims)

    world = reopen(tmp_path, folder)
    try:
        assert identities(world.profile) == ids
        assert not tombstones(world.profile)
        assert_one_table_each(world.profile, RUNS)
        assert archive_snapshot(folder) == snapshot
        before = world.overview()
        assert len(before.activities) == RUNS
        done = delete_use_case(world.profile)(victims)
        assert [t.identity for t in done] == victims
        assert delete_use_case(world.profile)(victims) == ()
        assert_one_table_each(world.profile, RUNS)
        assert len(tombstones(world.profile)) == len(TARGETS)
    finally:
        world.close()


def test_t_dati_04_a_kill_right_after_the_commit_of_a_deletion_leaves_it_whole_and_idempotent(
    tmp_path: Path, profile_at_rest: tuple[Path, list[ActivityIdentity]]
) -> None:
    folder, ids = profile_at_rest
    snapshot = archive_snapshot(folder)
    victims = [ids[i] for i in TARGETS]

    kill_during("delete", folder, "after_commit", victims)

    world = reopen(tmp_path, folder)
    try:
        assert identities(world.profile) == [i for n, i in enumerate(ids) if n not in TARGETS]
        assert {t.identity for t in tombstones(world.profile)} == set(victims)
        assert_one_table_each(world.profile, RUNS)
        assert archive_snapshot(folder) == snapshot
        assert delete_use_case(world.profile)(victims) == ()  # no double tombstone
        assert len(world.overview().activities) == RUNS - len(TARGETS)
    finally:
        world.close()


@pytest.mark.parametrize("point", ["after_tombstone_removed", "before_commit"])
def test_t_dati_04_a_kill_before_the_commit_of_a_restore_leaves_the_activities_deleted(
    tmp_path: Path, profile_at_rest: tuple[Path, list[ActivityIdentity]], point: str
) -> None:
    folder, ids = profile_at_rest
    victims = [ids[i] for i in TARGETS]
    world = reopen(tmp_path, folder)
    delete_use_case(world.profile)(victims)
    expected = world.overview()
    world.close()
    snapshot = archive_snapshot(folder)

    kill_during("restore", folder, point, victims)

    world = reopen(tmp_path, folder)
    try:
        assert {t.identity for t in tombstones(world.profile)} == set(victims)
        assert_one_table_each(world.profile, RUNS)
        assert archive_snapshot(folder) == snapshot
        assert same_pmc(world.overview().pmc, expected.pmc)
        restored = restore_use_case(world.profile)(victims)
        assert set(restored.restored) == set(victims)
        assert not tombstones(world.profile)
        assert identities(world.profile) == ids
        assert_one_table_each(world.profile, RUNS)
    finally:
        world.close()


def test_t_dati_04_a_kill_right_after_the_commit_of_a_restore_leaves_every_activity_back(
    tmp_path: Path, profile_at_rest: tuple[Path, list[ActivityIdentity]]
) -> None:
    """A restore is one transaction (all or none since the review of 2026-11-11): once it has
    committed, every activity is back and a second restore has nothing to do."""
    folder, ids = profile_at_rest
    first, second = (ids[i] for i in TARGETS)
    world = reopen(tmp_path, folder)
    full = world.overview()
    delete_use_case(world.profile)([first, second])
    world.close()

    kill_during("restore", folder, "after_commit", [first, second])

    world = reopen(tmp_path, folder)
    try:
        assert {first, second} <= set(identities(world.profile))
        assert tombstones(world.profile) == []
        assert_one_table_each(world.profile, RUNS)
        assert restore_use_case(world.profile)([first, second]).results == ()
        again = world.overview()
        assert again.activities == full.activities and same_pmc(again.pmc, full.pmc)
        assert_one_table_each(world.profile, RUNS)
    finally:
        world.close()


def test_t_dati_04_the_lock_of_a_killed_deleter_is_free_and_a_second_deleter_runs_to_the_end(
    tmp_path: Path, profile_at_rest: tuple[Path, list[ActivityIdentity]]
) -> None:
    folder, ids = profile_at_rest
    victims = [ids[0]]
    kill_during("delete", folder, "before_commit", victims)

    with Worker("delete", folder, "none", *keys(victims)) as worker:
        worker.expect_ready()
        assert worker.read_until("finished") == "finished"

    world = reopen(tmp_path, folder)
    try:
        assert {t.identity for t in tombstones(world.profile)} == set(victims)
        assert_one_table_each(world.profile, RUNS)
    finally:
        world.close()
