# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for schema v10: from the frozen v9 database to v9 (injuries and illnesses).

The v9 database is written by hand (v1 to v8 in the earlier files, v9 is the calendar's workout
backup). It is shown that v10 does not change after release, that a v9 profile migrates with no loss
anything and keeps a copy (`backup/ponos-v9-v10.sqlite3`), that the migration is one transaction
(killed at every statement the database is still v9), that the closed sets of v10 are the domain
enums, and that the database itself refuses a record that is not valid.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.injury.domain import (
    BodyZone,
    IllnessKind,
    Onset,
    PainFreeActivity,
    Side,
    WarningSign,
    defaults,
)
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import table_names, user_version
from tests.collaudo.test_fase5b_migrazione_v8 import FROZEN_V8, KEPT_V7, write_frozen_v7
from tests.fixtures.generators.collaudo_fase3_worker import DIED

# Migration v10 as it was released. It does not change, like v1 to v9.
FROZEN_V10 = (
    """CREATE TABLE injury (
    injury_id     TEXT    NOT NULL PRIMARY KEY
                          CHECK (length(injury_id) BETWEEN 1 AND 64
                                 AND injury_id NOT GLOB '*[^A-Za-z0-9_-]*'),
    kind          TEXT    NOT NULL CHECK (kind IN ('injury','illness')),
    started_on    TEXT    NOT NULL CHECK (date(started_on) IS started_on
                                          AND started_on >= '1900-01-01'),
    ended_on      TEXT    CHECK (ended_on IS NULL OR (date(ended_on) IS ended_on
                                                      AND ended_on >= started_on)),
    zone          TEXT    CHECK (zone IS NULL OR zone IN ('foot',
        'ankle',
        'achilles',
        'calf',
        'shin',
        'knee',
        'thigh',
        'hip',
        'lower_back',
        'upper_body')),
    side          TEXT    CHECK (side IS NULL OR side IN ('left','right','both')),
    onset         TEXT    CHECK (onset IS NULL OR onset IN ('sudden','gradual')),
    onset_day     TEXT    CHECK (onset_day IS NULL
                                 OR (date(onset_day) IS onset_day
                                     AND onset_day >= '1900-01-01')),
    pain_rest INTEGER CHECK (pain_rest IS NULL OR pain_rest BETWEEN 0 AND 10),
    pain_walking INTEGER CHECK (pain_walking IS NULL OR pain_walking BETWEEN 0 AND 10),
    pain_running INTEGER CHECK (pain_running IS NULL OR pain_running BETWEEN 0 AND 10),
    pain_morning INTEGER CHECK (pain_morning IS NULL OR pain_morning BETWEEN 0 AND 10),
    doctor_notes  TEXT    CHECK (doctor_notes IS NULL
                                 OR length(doctor_notes) <= 2000),
    reviewed_on   TEXT    CHECK (reviewed_on IS NULL OR (date(reviewed_on) IS reviewed_on
                                                         AND reviewed_on >= started_on)),
    illness_kind  TEXT    CHECK (illness_kind IS NULL OR illness_kind IN ('neck_up',
        'systemic')),
    CHECK (kind <> 'injury' OR (zone IS NOT NULL AND side IS NOT NULL AND onset IS NOT NULL
                                AND onset_day IS NOT NULL AND onset_day <= started_on
                                AND pain_rest IS NOT NULL AND pain_walking IS NOT NULL
                                AND pain_morning IS NOT NULL AND doctor_notes IS NOT NULL
                                AND illness_kind IS NULL)),
    CHECK (kind <> 'illness' OR (illness_kind IS NOT NULL AND zone IS NULL AND side IS NULL
                                 AND onset IS NULL AND onset_day IS NULL AND pain_rest IS NULL
                                 AND pain_walking IS NULL AND pain_running IS NULL
                                 AND pain_morning IS NULL AND doctor_notes IS NULL
                                 AND reviewed_on IS NULL))
) STRICT""",
    """CREATE INDEX injury_by_start ON injury (started_on, injury_id)""",
    """CREATE TABLE injury_pain_free (
    injury_id TEXT NOT NULL REFERENCES injury (injury_id) ON DELETE CASCADE,
    activity  TEXT NOT NULL CHECK (activity IN ('walking',
        'cycling',
        'swimming',
        'water_running')),
    PRIMARY KEY (injury_id, activity)
) STRICT""",
    """CREATE TABLE injury_checkin (
    injury_id   TEXT    NOT NULL REFERENCES injury (injury_id) ON DELETE CASCADE,
    day         TEXT    NOT NULL CHECK (date(day) IS day AND day >= '1900-01-01'),
    pain_during INTEGER CHECK (pain_during IS NULL OR pain_during BETWEEN 0 AND 10),
    pain_morning INTEGER CHECK (pain_morning BETWEEN 0 AND 10) NOT NULL,
    PRIMARY KEY (injury_id, day)
) STRICT""",
    """CREATE TABLE injury_sign (
    injury_id   TEXT NOT NULL REFERENCES injury (injury_id) ON DELETE CASCADE,
    checkin_day TEXT,
    sign        TEXT NOT NULL CHECK (sign IN ('night_pain',
        'strong_rest_pain',
        'swelling_or_deformity',
        'numbness_or_weakness',
        'cannot_bear_weight',
        'bone_pain_walking_or_night',
        'calf_swollen_and_warm',
        'cycling_stop_pain',
        'bladder_bowel_or_spreading_weakness',
        'fever',
        'general_malaise',
        'chest_pain',
        'palpitations',
        'fainting',
        'disproportionate_breathlessness')),
    FOREIGN KEY (injury_id, checkin_day) REFERENCES injury_checkin (injury_id, day)
        ON DELETE CASCADE
) STRICT""",
    """CREATE UNIQUE INDEX injury_sign_once
    ON injury_sign (injury_id, COALESCE(checkin_day, ''), sign)""",
)

KEPT_V9 = (*KEPT_V7, "workout_backup")
V10_TABLES = {"injury", "injury_pain_free", "injury_checkin", "injury_sign"}


def write_frozen_v9(path: Path) -> None:
    """A v9 database: the phase 5b one (track boxes) plus the calendar's workout backup."""
    write_frozen_v7(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in (*FROZEN_V8, *sqlite_schema.MIGRATIONS[8]):  # v8, then the calendar's v9
            raw.execute(statement)
        raw.execute("PRAGMA user_version = 9")
        raw.commit()


def test_t_dati_01_the_released_v10_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[9])

    assert released == tuple(squash(statement) for statement in FROZEN_V10)
    assert sqlite_schema.SCHEMA_VERSION >= 10


def test_t_dati_01_the_closed_sets_of_v10_are_the_domain_enums() -> None:
    assert {z.value for z in BodyZone} == sqlite_schema.V10_BODY_ZONES
    assert {s.value for s in Side} == sqlite_schema.V10_SIDES
    assert {o.value for o in Onset} == sqlite_schema.V10_ONSETS
    assert {a.value for a in PainFreeActivity} == sqlite_schema.V10_PAIN_FREE_ACTIVITIES
    assert {s.value for s in WarningSign} == sqlite_schema.V10_WARNING_SIGNS
    assert {k.value for k in IllnessKind} == sqlite_schema.V10_ILLNESS_KINDS
    assert defaults.MAX_NOTES_CHARS == sqlite_schema.V10_MAX_NOTES_CHARS


def test_t_dati_01_a_frozen_v9_database_is_migrated_to_v10_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v9(path)
    before = {table: rows(path, table) for table in KEPT_V9}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert opened.injuries.list_problems() == []  # the new tables are there, and empty
    finally:
        opened.close()

    assert {table: rows(path, table) for table in before} == before
    assert table_names(path) >= V10_TABLES
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v9-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 9
    assert V10_TABLES.isdisjoint(table_names(copy))
    assert {table: rows(copy, table) for table in KEPT_V9} == before


def test_t_dati_01_a_v7_database_goes_through_v8_v9_and_v10_in_one_round_with_one_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v7(path)

    SqliteProfileStorage().open(tmp_path).close()

    assert user_version(path) == sqlite_schema.SCHEMA_VERSION
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v7-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert table_names(path) >= V10_TABLES


def test_t_dati_01_a_v10_migration_that_fails_halfway_leaves_the_v9_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v9(path)
    broken_v10 = (sqlite_schema.MIGRATIONS[9][0], "CREATE TABLE questa_no (")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:9], broken_v10))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 9
    assert V10_TABLES.isdisjoint(table_names(path))  # not even the table that had succeeded
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[9]) + 1))
def test_t_dati_01_a_v9_database_killed_at_every_migration_statement_is_still_v9_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v9(path)
    before = {table: rows(path, table) for table in KEPT_V9}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 9  # the transaction was not committed
    assert V10_TABLES.isdisjoint(table_names(path))  # not even the statements that had succeeded
    assert {table: rows(path, table) for table in KEPT_V9} == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


# --- The database refuses a record that is not valid ---------------------------------------------


def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v9(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    connection = sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


INJURY = (
    "INSERT INTO injury (injury_id, kind, started_on, ended_on, zone, side, onset, onset_day, "
    "pain_rest, pain_walking, pain_running, pain_morning, doctor_notes, reviewed_on, "
    "illness_kind) VALUES ({})"
)
GOOD_INJURY = (
    "'i1', 'injury', '2026-03-02', NULL, 'knee', 'left', 'sudden', '2026-03-01', 1, 2, NULL, 1, "
    "'', NULL, NULL"
)
NULLS = ", ".join(["NULL"] * 10)  # the ten columns of an injury that an illness has not
GOOD_ILLNESS = f"'s1', 'illness', '2026-03-02', NULL, {NULLS}, 'neck_up'"


def test_the_database_accepts_a_valid_injury_and_a_valid_illness(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(INJURY.format(GOOD_INJURY))
        raw.execute(INJURY.format(GOOD_ILLNESS))
        raw.commit()


def bad(old: str, new: str) -> str:
    assert old in GOOD_INJURY
    return GOOD_INJURY.replace(old, new, 1)


BAD_INJURIES = [
    bad("'i1'", "''"),  # empty id
    bad("'i1'", "'a b'"),  # a character outside the id alphabet
    bad("'i1'", "'" + "x" * 65 + "'"),  # id too long
    bad("'injury'", "'sprain'"),  # unknown kind
    bad("'injury'", "'illness'"),  # an illness with a zone
    bad("'2026-03-02'", "'2026-02-30'"),  # not a date
    bad("'2026-03-02'", "'1899-12-31'"),  # before 1900
    bad("NULL, 'knee'", "'2026-03-01', 'knee'"),  # closed before it started
    bad("'knee'", "'elbow'"),  # unknown zone
    bad("'left'", "'middle'"),  # unknown side
    bad("'sudden'", "'slow'"),  # unknown onset
    bad("'2026-03-01'", "'2026-03-05'"),  # it began after it was opened
    bad("1, 2, NULL, 1", "11, 2, NULL, 1"),  # rest pain above 10
    bad("1, 2, NULL, 1", "1, -1, NULL, 1"),  # walking pain below 0
    bad("1, 2, NULL, 1", "1, 2, 12, 1"),  # running pain above 10
    bad("1, 2, NULL, 1", "1, 2, NULL, 99"),  # morning pain above 10
    bad("1, 2, NULL, 1", "NULL, 2, NULL, 1"),  # a missing rest pain
    bad("'', NULL, NULL", "NULL, NULL, NULL"),  # a missing note (empty text is the empty note)
    bad("'', NULL, NULL", "'" + "x" * 2001 + "', NULL, NULL"),  # notes too long
    bad("'', NULL, NULL", "'', '2026-03-01', NULL"),  # reviewed before the start
    bad("'', NULL, NULL", "'', NULL, 'neck_up'"),  # an injury with an illness kind
]


@pytest.mark.parametrize("values", BAD_INJURIES)
def test_the_database_refuses_an_invalid_injury(tmp_path: Path, values: str) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(INJURY.format(values))


BAD_ILLNESSES = [
    GOOD_ILLNESS.replace("'neck_up'", "NULL"),  # no kind
    GOOD_ILLNESS.replace("'neck_up'", "'flu'"),  # unknown kind
    GOOD_ILLNESS.replace(NULLS, NULLS.replace("NULL", "'knee'", 1)),  # an illness with a zone
    GOOD_ILLNESS.replace("'2026-03-02', NULL", "'2026-03-02', '2026-03-01'", 1),  # ends before
]


@pytest.mark.parametrize("values", BAD_ILLNESSES)
def test_the_database_refuses_an_invalid_illness(tmp_path: Path, values: str) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(INJURY.format(values))


def test_the_database_refuses_orphans_unknown_values_and_duplicates(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(INJURY.format(GOOD_INJURY))
        statements = [
            "INSERT INTO injury_pain_free VALUES ('nobody', 'walking')",  # no such injury
            "INSERT INTO injury_pain_free VALUES ('i1', 'skiing')",  # not in the closed set
            "INSERT INTO injury_checkin VALUES ('nobody', '2026-03-03', 1, 1)",
            "INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', 11, 1)",  # pain above 10
            "INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', 1, NULL)",  # morning missing
            "INSERT INTO injury_checkin VALUES ('i1', '2026-13-03', 1, 1)",  # not a date
            "INSERT INTO injury_sign VALUES ('nobody', NULL, 'fever')",
            "INSERT INTO injury_sign VALUES ('i1', NULL, 'mumps')",  # not a warning sign
            "INSERT INTO injury_sign VALUES ('i1', '2026-03-03', 'fever')",  # no such check-in
        ]
        for statement in statements:
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(statement)
        raw.execute("INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', NULL, 1)")
        raw.execute("INSERT INTO injury_checkin VALUES ('i1', '2026-03-04', 2, 1)")
        raw.execute("INSERT INTO injury_sign VALUES ('i1', NULL, 'fever')")
        raw.execute("INSERT INTO injury_sign VALUES ('i1', '2026-03-03', 'fever')")
        for statement in (
            "INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', 1, 1)",  # one a day
            "INSERT INTO injury_sign VALUES ('i1', NULL, 'fever')",  # one sign of the questionnaire
            "INSERT INTO injury_sign VALUES ('i1', '2026-03-03', 'fever')",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                raw.execute(statement)


def test_deleting_an_injury_deletes_everything_that_belongs_to_it(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(INJURY.format(GOOD_INJURY))
        raw.execute("INSERT INTO injury_pain_free VALUES ('i1', 'walking')")
        raw.execute("INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', NULL, 1)")
        raw.execute("INSERT INTO injury_sign VALUES ('i1', '2026-03-03', 'fever')")
        raw.execute("DELETE FROM injury WHERE injury_id = 'i1'")
        counts = [
            raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
            for table in ("injury_pain_free", "injury_checkin", "injury_sign")
        ]
    assert counts == [0, 0, 0]


def test_deleting_a_check_in_deletes_its_signs_and_not_the_questionnaire_ones(
    tmp_path: Path,
) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(INJURY.format(GOOD_INJURY))
        raw.execute("INSERT INTO injury_checkin VALUES ('i1', '2026-03-03', NULL, 1)")
        raw.execute("INSERT INTO injury_sign VALUES ('i1', '2026-03-03', 'fever')")
        raw.execute("INSERT INTO injury_sign VALUES ('i1', NULL, 'night_pain')")
        raw.execute("DELETE FROM injury_checkin WHERE day = '2026-03-03'")
        left = raw.execute("SELECT sign FROM injury_sign").fetchall()
    assert left == [("night_pain",)]
