# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding for the phase 9 acceptance campaign: deleting and restoring activities on a real
profile (SQLite, on-disk archive), with the checks that an identity is in exactly one table and
that the archive is never touched by a deletion.

The athlete is invented ("Atleta di prova"); the runs are synthetic FIT files.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.deletion import DeleteActivities, RestoreActivities
from ponos_coach.activities.domain import ActivityIdentity, DeletedActivity, identity_key
from ponos_coach.load.domain.pmc import PmcSeries
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR, PROFILE_DATABASE_NAME
from tests.activities.import_fakes import FixedClock
from tests.collaudo.fase5_support import World
from tests.collaudo.support import RealProfile

DELETED_AT = datetime(2026, 7, 2, 9, 30, tzinfo=UTC)


def delete_use_case(profile: RealProfile) -> DeleteActivities:
    session = profile.session
    return DeleteActivities(session.activities, session.deleted_activities, FixedClock(DELETED_AT))


def restore_use_case(profile: RealProfile) -> RestoreActivities:
    session = profile.session
    return RestoreActivities(
        session.activities, session.deleted_activities, profile.archive, decode_stored_activity
    )


def identities(profile: RealProfile) -> list[ActivityIdentity]:
    """The identities of the index, by ascending start."""
    return [record.identity for record in profile.records()]


def tombstones(profile: RealProfile) -> Sequence[DeletedActivity]:
    return profile.session.deleted_activities.list_latest_first()


def keys(items: Sequence[ActivityIdentity]) -> list[str]:
    return [identity_key(identity) for identity in items]


def table_keys(folder: Path) -> tuple[set[str], set[str]]:
    """The identity keys of `activities` and of `deleted_activity`, read from the file."""
    uri = f"file:{(folder / PROFILE_DATABASE_NAME).as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        alive = {r[0] for r in connection.execute("SELECT identity_key FROM activities")}
        dead = {r[0] for r in connection.execute("SELECT identity_key FROM deleted_activity")}
    return alive, dead


def archive_snapshot(folder: Path) -> dict[str, bytes]:
    root = folder / PROFILE_ARCHIVE_DIR
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def archived_file_of(profile: RealProfile, tombstone: DeletedActivity) -> Path:
    return profile.folder / PROFILE_ARCHIVE_DIR / tombstone.archived.relative_path


def assert_one_table_each(profile: RealProfile, expected_total: int) -> None:
    """Every identity is in exactly one of the two tables, and nothing is lost."""
    alive, dead = table_keys(profile.folder)
    assert not alive & dead
    assert len(alive) + len(dead) == expected_total
    assert alive == set(keys(identities(profile)))


def import_runs(world: World, count: int, *, first_offset: int = 60) -> list[ActivityIdentity]:
    """Imports `count` runs on consecutive days before `world.today`; returns their identities."""
    return [
        world.import_run(world.today - timedelta(days=first_offset - n), bpm=140 + n % 20).identity
        for n in range(count)
    ]


def same_pmc(first: PmcSeries, second: PmcSeries) -> bool:
    """Equal day by day, bit for bit (the arrays make `==` of the dataclass unusable)."""
    return (
        first.first_day == second.first_day
        and np.array_equal(first.atl, second.atl)
        and np.array_equal(first.ctl, second.ctl)
        and np.array_equal(first.tsb, second.tsb)
    )
