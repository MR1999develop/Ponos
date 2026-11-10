# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Average and maximum HR in the load cache: the migration (T-DATI-01) and the model version.

The migration is found by identity (`LOAD_HEART_RATE_MIGRATION`), not by its number.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.load.domain import LOAD_MODEL_VERSION, LoadFingerprint
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.storage.schema_load_heart_rate import LOAD_HEART_RATE_MIGRATION
from ponos_coach.storage.sqlite_database import create_database
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION
from tests.builders import fit_identity, make_record

PROFILE_ID = "00000000-0000-4000-8000-000000000001"
META = {"profile_id": PROFILE_ID, "name": "Atleta di prova", "timezone": "Europe/Rome"}
FROZEN = (
    "DELETE FROM load_cache",
    "ALTER TABLE load_cache ADD COLUMN avg_bpm INTEGER "
    "CHECK (avg_bpm IS NULL OR avg_bpm BETWEEN 20 AND 250)",
    "ALTER TABLE load_cache ADD COLUMN max_bpm INTEGER "
    "CHECK (max_bpm IS NULL OR max_bpm BETWEEN 20 AND 250)",
)
FINGERPRINT = "a" * 64


def _squash(text: str) -> str:
    return "".join(text.split())


def _position() -> int:
    return next(i for i, m in enumerate(MIGRATIONS) if m is LOAD_HEART_RATE_MIGRATION)


def test_the_released_migration_has_not_changed() -> None:
    assert tuple(_squash(s) for s in LOAD_HEART_RATE_MIGRATION) == tuple(_squash(s) for s in FROZEN)


def test_the_load_model_version_moved_past_the_one_without_heart_rate() -> None:
    assert LOAD_MODEL_VERSION >= 4  # 3 had no HR in the cache: the fingerprint changes


def test_a_profile_from_before_the_migration_loses_its_cached_loads_and_gets_the_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    before = _position()
    with closing(sqlite3.connect(path)) as raw:
        for migration in MIGRATIONS[:before]:
            for statement in migration:
                raw.execute(statement)
        raw.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", list(META.items()))
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()
    storage = SqliteProfileStorage()
    with closing(storage.open(tmp_path)) as opened:
        opened.activities.add(make_record())
    # Rewind: a cached load in the old shape, as the released program would have left it.
    with closing(sqlite3.connect(path)) as raw:
        raw.execute("ALTER TABLE load_cache DROP COLUMN avg_bpm")
        raw.execute("ALTER TABLE load_cache DROP COLUMN max_bpm")
        raw.execute(
            "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, used_samples, "
            "discarded_samples, excluded_seconds, source, zone1_s, zone2_s, zone3_s, zone4_s, "
            "zone5_s) SELECT id, ?, 1, 0, 1, 1, 0, 0, 'heart_rate', 0, 0, 0, 0, 0 "
            "FROM activities",
            (FINGERPRINT,),
        )
        raw.execute(f"PRAGMA user_version = {before}")
        raw.commit()

    with closing(storage.open(tmp_path)) as opened:
        assert opened.info.format_version == SCHEMA_VERSION
        assert opened.load_cache.get(fit_identity(), LoadFingerprint(FINGERPRINT)) is None
    with closing(sqlite3.connect(path)) as raw:
        columns = {row[1] for row in raw.execute("PRAGMA table_info(load_cache)")}
    assert {"avg_bpm", "max_bpm"} <= columns


_INSERT_WITH = {
    "avg_bpm": (
        "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, used_samples, "
        "discarded_samples, excluded_seconds, source, avg_bpm) "
        "SELECT id, ?, 1, 0, 1, 1, 0, 0, 'heart_rate', ? FROM activities"
    ),
    "max_bpm": (
        "INSERT INTO load_cache (activity_id, fingerprint, trimp, hr_tss, load, used_samples, "
        "discarded_samples, excluded_seconds, source, max_bpm) "
        "SELECT id, ?, 1, 0, 1, 1, 0, 0, 'heart_rate', ? FROM activities"
    ),
}


@pytest.mark.parametrize("column", ["avg_bpm", "max_bpm"])
@pytest.mark.parametrize("value", [19, 251, 100.5, "high"])
def test_the_database_itself_refuses_an_invalid_heart_rate(
    tmp_path: Path, column: str, value: object
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    create_database(path, META).close()
    with closing(SqliteProfileStorage().open(tmp_path)) as opened:
        opened.activities.add(make_record())
    with closing(sqlite3.connect(path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(_INSERT_WITH[column], (FINGERPRINT, value))
