# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 for phase 5: from the frozen v5 database to v6 (goals, availability, plan).

The v5 database below is written by hand, independent of the code (v1 to v4 are in the earlier
files). It is shown that v6 does not change after release, that a v5 profile migrates without
losing anything and keeps a copy (`backup/ponos-v5-v6.sqlite3`), that the migration is one
transaction (killed at every statement the database is still v5), that the database itself
refuses what the domain would refuse, and that a plan read back from it is input: tampered,
oversized or malformed text becomes "unreadable", never an exception.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal
from ponos_coach.planning.domain import Plan, PlanRefusal, RefusalReason, build_plan
from ponos_coach.planning.plan_codec import digest
from ponos_coach.planning.ports import StoredPlan, UnreadablePlan
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from tests.collaudo.test_database_del_profilo import rows, squash
from tests.collaudo.test_fase3_dati_personali import run_worker_until_death
from tests.collaudo.test_fase3_migrazione import table_names, user_version
from tests.collaudo.test_fase4_migrazione import FROZEN_V5, KEPT, write_frozen_v4
from tests.fixtures.generators.collaudo_fase3_worker import DIED
from tests.planning.builders import TODAY, inputs, race

# Migration v6 as it was released. It does not change, like v1 to v5.
FROZEN_V6 = (
    """CREATE TABLE goal (
        goal_id TEXT NOT NULL PRIMARY KEY
            CHECK (length(goal_id) BETWEEN 1 AND 64 AND goal_id NOT GLOB '*[^A-Za-z0-9_-]*'),
        kind TEXT NOT NULL CHECK (kind IN ('race','fitness')),
        goal_date TEXT NOT NULL
            CHECK (date(goal_date) IS goal_date AND goal_date >= '1900-01-01'),
        priority TEXT CHECK (priority IS NULL OR priority IN ('A','B','C')),
        sport TEXT CHECK (sport IS NULL OR sport IN
            ('running','trail','cycling','strength','hiking','walking','other')),
        distance_m INTEGER CHECK (distance_m IS NULL OR distance_m BETWEEN 400 AND 500000),
        target_time_s INTEGER
            CHECK (target_time_s IS NULL OR target_time_s BETWEEN 60 AND 604800),
        ctl REAL CHECK (ctl IS NULL OR (ctl >= 0 AND ctl <= 200)),
        CHECK (kind <> 'race' OR (priority IS NOT NULL AND sport IS NOT NULL
                                  AND distance_m IS NOT NULL AND ctl IS NULL)),
        CHECK (kind <> 'fitness' OR (ctl IS NOT NULL AND priority IS NULL AND sport IS NULL
                                     AND distance_m IS NULL AND target_time_s IS NULL))
    ) STRICT""",
    "CREATE UNIQUE INDEX goal_one_race_per_day ON goal (goal_date) WHERE kind = 'race'",
    "CREATE INDEX goal_by_date ON goal (goal_date, goal_id)",
    """CREATE TABLE weekly_availability (
        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        day0_min INTEGER NOT NULL CHECK (day0_min BETWEEN 0 AND 720),
        day1_min INTEGER NOT NULL CHECK (day1_min BETWEEN 0 AND 720),
        day2_min INTEGER NOT NULL CHECK (day2_min BETWEEN 0 AND 720),
        day3_min INTEGER NOT NULL CHECK (day3_min BETWEEN 0 AND 720),
        day4_min INTEGER NOT NULL CHECK (day4_min BETWEEN 0 AND 720),
        day5_min INTEGER NOT NULL CHECK (day5_min BETWEEN 0 AND 720),
        day6_min INTEGER NOT NULL CHECK (day6_min BETWEEN 0 AND 720),
        long_day INTEGER NOT NULL CHECK (long_day BETWEEN 0 AND 6),
        max_week_min INTEGER NOT NULL CHECK (max_week_min BETWEEN 0 AND 6000)
    ) STRICT""",
    """CREATE TABLE session_choice (
        sport TEXT NOT NULL CHECK (sport IN
            ('running','trail','cycling','strength','hiking','walking','other')),
        session_type TEXT NOT NULL CHECK (session_type IN
            ('easy','long','medium','threshold','vo2max','recovery','strength','alternative')),
        PRIMARY KEY (sport, session_type)
    ) STRICT""",
    """CREATE TABLE plan_state (
        id INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
        kind TEXT NOT NULL CHECK (kind IN ('plan','refusal')),
        generated_on TEXT NOT NULL
            CHECK (date(generated_on) IS generated_on AND generated_on >= '1900-01-01'),
        method_version INTEGER CHECK (method_version IS NULL OR method_version >= 1),
        plan_json TEXT CHECK (plan_json IS NULL OR length(plan_json) BETWEEN 2 AND 16777216),
        plan_sha256 TEXT CHECK (plan_sha256 IS NULL OR length(plan_sha256) = 64),
        refusal_reason TEXT CHECK (refusal_reason IS NULL OR refusal_reason IN
            ('no_goals','goal_not_in_future','goals_on_same_day','no_availability',
             'no_session_types')),
        refusal_goal_ids TEXT CHECK (refusal_goal_ids IS NULL OR length(refusal_goal_ids) <= 65536),
        CHECK (kind <> 'plan' OR (method_version IS NOT NULL AND plan_json IS NOT NULL
                                  AND plan_sha256 IS NOT NULL AND refusal_reason IS NULL
                                  AND refusal_goal_ids IS NULL)),
        CHECK (kind <> 'refusal' OR (refusal_reason IS NOT NULL AND refusal_goal_ids IS NOT NULL
                                     AND method_version IS NULL AND plan_json IS NULL
                                     AND plan_sha256 IS NULL))
    ) STRICT""",
    """CREATE TABLE plan_past_day (
        day TEXT NOT NULL PRIMARY KEY CHECK (date(day) IS day AND day >= '1900-01-01'),
        planned_on TEXT NOT NULL CHECK (date(planned_on) IS planned_on AND planned_on < day),
        day_json TEXT NOT NULL CHECK (length(day_json) BETWEEN 2 AND 65536),
        day_sha256 TEXT NOT NULL CHECK (length(day_sha256) = 64)
    ) STRICT""",
    """CREATE TRIGGER plan_past_day_is_fixed BEFORE UPDATE ON plan_past_day
    BEGIN
        SELECT RAISE(ABORT, 'plan_past_day is history');
    END""",
    """CREATE TRIGGER plan_past_day_is_kept BEFORE DELETE ON plan_past_day
    BEGIN
        SELECT RAISE(ABORT, 'plan_past_day is history');
    END""",
)

V6_TABLES = {"goal", "weekly_availability", "session_choice", "plan_state", "plan_past_day"}
SHA = "0" * 64


def write_frozen_v5(path: Path) -> None:
    """A v5 database as the phase 4 app wrote it, with data in every table (without `src`)."""
    write_frozen_v4(path)
    with closing(sqlite3.connect(path)) as raw:
        for statement in FROZEN_V5:
            raw.execute(statement)
        raw.execute(
            "INSERT INTO threshold_test VALUES "
            "('fit:255:1:1:2026-03-02T07:00:00+00:00', '2026-03-02', 1772435400000, 168, 300, 1)"
        )
        raw.execute("PRAGMA user_version = 5")
        raw.commit()


KEPT_V5 = (*KEPT, "threshold_test")


def test_t_dati_01_the_released_v6_migration_has_not_changed() -> None:
    released = tuple(squash(statement) for statement in sqlite_schema.MIGRATIONS[5])

    assert released == tuple(squash(statement) for statement in FROZEN_V6)
    assert sqlite_schema.SCHEMA_VERSION >= 6


def test_the_closed_sets_of_v6_are_the_domain_enums() -> None:
    from ponos_coach.goals.domain import SessionType  # noqa: PLC0415 - only this test needs it

    assert {p.value for p in Priority} == sqlite_schema.V6_PRIORITIES
    assert {t.value for t in SessionType} == sqlite_schema.V6_SESSION_TYPES
    assert {r.value for r in RefusalReason} == sqlite_schema.V6_REFUSAL_REASONS
    assert {s.value for s in Sport} == sqlite_schema.V1_SPORTS


def test_t_dati_01_a_frozen_v5_database_is_migrated_to_v6_with_data_intact_and_a_copy(
    tmp_path: Path,
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v5(path)
    before = {table: rows(path, table) for table in KEPT_V5}

    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.info.format_version == sqlite_schema.SCHEMA_VERSION
        assert len(opened.threshold_tests.list_tests()) == 1
    finally:
        opened.close()

    after = {table: rows(path, table) for table in KEPT_V5}
    # v7 adds a column at the end of the test history: the v5 columns are what they were
    after["threshold_test"] = [row[:6] for row in after["threshold_test"]]
    assert after == before
    assert table_names(path) >= V6_TABLES
    [copy] = sorted((tmp_path / "backup").glob("*"))
    assert copy.name == f"ponos-v5-v{sqlite_schema.SCHEMA_VERSION}.sqlite3"
    assert user_version(copy) == 5
    assert V6_TABLES.isdisjoint(table_names(copy))
    assert {table: rows(copy, table) for table in KEPT_V5} == before


def test_t_dati_01_a_v5_profile_starts_with_no_goals_no_plan_and_keeps_what_is_saved(
    tmp_path: Path,
) -> None:
    write_frozen_v5(tmp_path / PROFILE_DATABASE_NAME)
    goal = RaceGoal(GoalId("a"), date(2026, 9, 6), Priority.A, Sport.RUNNING, 21_097, 6000)
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        assert opened.goals.list_goals() == []
        assert opened.availability.get() is None
        assert opened.plans.current() is None
        opened.goals.add(goal)
        opened.plans.replace_current(PlanRefusal(RefusalReason.NO_AVAILABILITY), TODAY)
    finally:
        opened.close()

    reopened = SqliteProfileStorage().open(tmp_path)
    try:
        assert list(reopened.goals.list_goals()) == [goal]
        assert reopened.plans.current() == StoredPlan(
            PlanRefusal(RefusalReason.NO_AVAILABILITY), TODAY
        )
    finally:
        reopened.close()


def test_t_dati_01_a_v6_migration_that_fails_halfway_leaves_the_v5_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v5(path)
    tables_before = table_names(path)
    broken_v6 = (sqlite_schema.MIGRATIONS[5][0], "CREATE TABLE questa_no (")
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (*sqlite_schema.MIGRATIONS[:5], broken_v6))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 5
    assert table_names(path) == tables_before  # not even the table that had succeeded
    monkeypatch.undo()
    SqliteProfileStorage().open(tmp_path).close()  # the good round succeeds
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


@pytest.mark.parametrize("kill_at", range(1, len(sqlite_schema.MIGRATIONS[5]) + 1))
def test_t_dati_01_a_v5_database_killed_at_every_migration_statement_is_still_v5_then_migrates(
    tmp_path: Path, kill_at: int
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v5(path)
    before = {table: rows(path, table) for table in KEPT_V5}

    assert run_worker_until_death(tmp_path, kill_at) == DIED

    assert user_version(path) == 5  # the transaction was not committed
    assert V6_TABLES.isdisjoint(table_names(path))
    assert {table: rows(path, table) for table in KEPT_V5} == before
    SqliteProfileStorage().open(tmp_path).close()
    assert user_version(path) == sqlite_schema.SCHEMA_VERSION


def test_t_dati_01_a_migrated_profile_opens_again_without_a_second_copy(tmp_path: Path) -> None:
    write_frozen_v5(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    first = sorted((tmp_path / "backup").glob("*"))

    SqliteProfileStorage().open(tmp_path).close()

    assert sorted((tmp_path / "backup").glob("*")) == first


# --- The database refuses what the domain refuses ---------------------------------------------
def migrated(tmp_path: Path) -> sqlite3.Connection:
    write_frozen_v5(tmp_path / PROFILE_DATABASE_NAME)
    SqliteProfileStorage().open(tmp_path).close()
    return sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)


def goal_row(
    goal_id: str = "g1",
    *,
    kind: str = "race",
    day: str = "2026-09-06",
    priority: str = "'A'",
    sport: str = "'running'",
    distance: str = "10000",
    target: str = "NULL",
    ctl: str = "NULL",
) -> str:
    return (
        "INSERT INTO goal VALUES "  # noqa: S608 - test data
        f"('{goal_id}', '{kind}', '{day}', {priority}, {sport}, {distance}, {target}, {ctl})"
    )


def fitness_row(
    goal_id: str = "f1", day: str = "2026-09-06", ctl: str = "55", extra_priority: str = "NULL"
) -> str:
    return goal_row(
        goal_id,
        kind="fitness",
        day=day,
        priority=extra_priority,
        sport="NULL",
        distance="NULL",
        ctl=ctl,
    )


BAD_GOALS = [
    goal_row(goal_id=""),
    goal_row(goal_id="a" * 65),
    goal_row(goal_id="has space"),
    goal_row(goal_id="semi;colon"),
    goal_row(kind="trip"),
    goal_row(day="2026-02-30"),
    goal_row(day="1899-12-31"),
    goal_row(priority="'D'"),
    goal_row(priority="NULL"),  # a race has a priority
    goal_row(sport="NULL"),
    goal_row(sport="'swimming'"),
    goal_row(distance="399"),
    goal_row(distance="500001"),
    goal_row(distance="NULL"),
    goal_row(target="59"),
    goal_row(target="604801"),
    goal_row(ctl="50"),  # a race has no CTL
    fitness_row(ctl="-0.1"),
    fitness_row(ctl="200.1"),
    fitness_row(ctl="NULL"),
    fitness_row(extra_priority="'A'"),  # a form goal has no priority
]
GOOD_GOALS = [
    goal_row(goal_id="a" * 64),
    goal_row(goal_id="A_b-9"),
    goal_row(day="1900-01-01"),
    goal_row(distance="400"),
    goal_row(distance="500000"),
    goal_row(target="60"),
    goal_row(target="604800"),
    fitness_row(ctl="0"),
    fitness_row(ctl="200"),
]


@pytest.mark.parametrize("statement", BAD_GOALS)
def test_the_database_refuses_goals_outside_the_domain_limits(
    tmp_path: Path, statement: str
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(statement)


@pytest.mark.parametrize("statement", GOOD_GOALS)
def test_the_database_accepts_goals_on_the_limits(tmp_path: Path, statement: str) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(statement)


def test_the_database_allows_one_race_a_day_even_to_a_direct_write(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(goal_row("g1", day="2026-09-06"))
        raw.execute(goal_row("g2", day="2026-09-07"))
        raw.execute(fitness_row("f1", day="2026-09-06"))
        raw.execute(fitness_row("f2", day="2026-09-06"))  # form goals may share a day

        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(goal_row("g3", day="2026-09-06"))
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("UPDATE goal SET goal_date = '2026-09-06' WHERE goal_id = 'g2'")


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO weekly_availability VALUES (2, 1, 1, 0, 60)",
        "INSERT INTO weekly_availability VALUES (1, 128, 1, 0, 60)",
        "INSERT INTO weekly_availability VALUES (1, -1, 1, 0, 60)",
        "INSERT INTO weekly_availability VALUES (1, 1, 1, 7, 60)",
        "INSERT INTO weekly_availability VALUES (1, 1, 1, -1, 60)",
        "INSERT INTO weekly_availability VALUES (1, 1, 1, 0, 6001)",
        "INSERT INTO weekly_availability VALUES (1, 1, 0, 0, 60)",  # days but no session
        "INSERT INTO weekly_availability VALUES (1, 3, 3, 0, 60)",  # 2 days, 3 sessions: 3 > 2
        "INSERT INTO weekly_availability VALUES (1, 5, 1, 1, 60)",  # the long day is not a day
        "INSERT INTO weekly_availability VALUES (1, 0, 0, 0, 60)",  # time but no day
        "INSERT INTO weekly_availability VALUES (1, 0, 1, 0, 0)",  # a session but no day
        "INSERT INTO session_choice VALUES ('swimming', 'easy')",
        "INSERT INTO session_choice VALUES ('running', 'sprint')",
    ],
)
def test_the_database_refuses_availability_and_session_choices_outside_the_domain(
    tmp_path: Path, statement: str
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(statement)


def test_the_database_accepts_the_availability_limits_and_refuses_a_choice_twice(
    tmp_path: Path,
) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute("INSERT INTO weekly_availability VALUES (1, 127, 7, 6, 6000)")
        raw.execute(
            "UPDATE weekly_availability SET days_mask = 0, sessions_per_week = 0, max_week_min = 0"
        )  # the empty one
        raw.execute("INSERT INTO session_choice VALUES ('running', 'easy')")

        with pytest.raises(sqlite3.IntegrityError):
            raw.execute("INSERT INTO session_choice VALUES ('running', 'easy')")


def state_row(
    *,
    row_id: int = 1,
    kind: str = "refusal",
    generated_on: str = "2026-03-02",
    version: int | None = None,
    text: str | None = None,
    sha: str | None = None,
    reason: str | None = "no_goals",
    ids: str | None = "",
) -> tuple[str, tuple[object, ...]]:
    return (
        "INSERT INTO plan_state VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (row_id, kind, generated_on, version, text, sha, reason, ids),
    )


def past_row(
    day: str = "2026-03-03", planned_on: str = "2026-03-02", text: str = "{}", sha: str = SHA
) -> tuple[str, tuple[object, ...]]:
    return ("INSERT INTO plan_past_day VALUES (?, ?, ?, ?)", (day, planned_on, text, sha))


def plan_kind(**changes: object) -> tuple[str, tuple[object, ...]]:
    base: dict[str, object] = {
        "kind": "plan",
        "version": 1,
        "text": "{}",
        "sha": SHA,
        "reason": None,
        "ids": None,
    }
    return state_row(**{**base, **changes})  # type: ignore[arg-type]


BAD_STATES = [
    state_row(row_id=2),
    state_row(kind="other"),
    plan_kind(version=None),
    plan_kind(text=None),
    plan_kind(sha="short"),
    plan_kind(reason="no_goals", ids=""),
    state_row(reason="because"),
    state_row(ids=None),
    state_row(version=1),
    state_row(generated_on="2026-02-30"),
    past_row(planned_on="2026-03-03"),  # planned on the day itself
    past_row(planned_on="2026-03-04"),  # planned after it
    past_row(text=""),
    past_row(sha="short"),
    past_row(day="2026-02-30", planned_on="2026-02-01"),
]


@pytest.mark.parametrize("statement", BAD_STATES)
def test_the_database_refuses_a_plan_state_or_past_day_that_does_not_hold_together(
    tmp_path: Path, statement: tuple[str, tuple[object, ...]]
) -> None:
    with closing(migrated(tmp_path)) as raw, pytest.raises(sqlite3.IntegrityError):
        raw.execute(*statement)


def test_the_database_keeps_a_frozen_day_as_history(tmp_path: Path) -> None:
    with closing(migrated(tmp_path)) as raw:
        raw.execute(*past_row())

        with pytest.raises(sqlite3.DatabaseError, match="history"):
            raw.execute("UPDATE plan_past_day SET day_json = '[]'")
        with pytest.raises(sqlite3.DatabaseError, match="history"):
            raw.execute("DELETE FROM plan_past_day")

        assert raw.execute("SELECT COUNT(*) FROM plan_past_day").fetchone() == (1,)


# --- A plan read back from the database is input ------------------------------------------------
def a_plan() -> Plan:
    built = build_plan(inputs([race("a", date(2026, 4, 5))]), TODAY)
    assert isinstance(built, Plan)
    return built


def stored_after(tmp_path: Path, statements: list[tuple[str, tuple[object, ...]]]) -> object:
    """What `current()` says after the given raw writes on a migrated database."""
    with closing(migrated(tmp_path)) as raw:
        for statement, parameters in statements:
            raw.execute(statement, parameters)
        raw.commit()
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        return opened.plans.current()
    finally:
        opened.close()


def plan_state(
    text: str, sha: str | None = None, version: int = 1, generated_on: str = "2026-03-02"
) -> list[tuple[str, tuple[object, ...]]]:
    return [
        (
            "INSERT INTO plan_state VALUES (1, 'plan', ?, ?, ?, ?, NULL, NULL)",
            (generated_on, version, text, digest(text) if sha is None else sha),
        )
    ]


def test_a_plan_written_by_hand_exactly_as_the_store_writes_it_is_read_back(
    tmp_path: Path,
) -> None:
    plan = a_plan()

    stored = stored_after(tmp_path, plan_state(plan.to_json(), version=plan.method_version))

    assert isinstance(stored, StoredPlan)
    assert isinstance(stored.state, Plan)
    assert stored.state.to_json() == plan.to_json()


def _mutated(plan: Plan, mutate: object) -> str:
    data = json.loads(plan.to_json())
    assert callable(mutate)
    mutate(data)
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


HOSTILE: dict[str, object] = {
    "an unknown field": lambda d: d.update(surprise=1),
    "a missing field": lambda d: d.pop("warnings"),
    "another type name": lambda d: d.update(_type="Other"),
    "an unknown enum value": lambda d: d["days"][0].update(phase="turbo"),
    "a bool for a number": lambda d: d["days"][0].update(load=True),
    "a string for a number": lambda d: d["days"][0].update(load="12"),
    "a date in another form": lambda d: d.update(generated_on="20260302"),
    "an impossible date": lambda d: d.update(generated_on="2026-02-30"),
    "a block with an inverted range": lambda d: d["days"][0]["session"]["blocks"][0].update(
        hr_low_bpm=200, hr_high_bpm=100
    ),
    "a list for an object": lambda d: d.update(inputs=[]),
    "a null for a required object": lambda d: d.update(inputs=None),
    "nested far too deep": lambda d: d.update(warnings=[[[[[[[[[[[[1]]]]]]]]]]]]),
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_a_plan_with_valid_checksum_but_hostile_content_is_unreadable_not_an_error(
    tmp_path: Path, name: str
) -> None:
    plan = a_plan()
    text = _mutated(plan, HOSTILE[name])

    stored = stored_after(tmp_path, plan_state(text, version=plan.method_version))

    assert stored == UnreadablePlan()


@pytest.mark.parametrize(
    "text",
    ["not json", "[]", "null", '{"_type":"Plan"}', "NaN", '{"a":Infinity}', "{" * 5000, "﻿{}"],
)
def test_text_that_is_not_a_plan_is_unreadable_not_an_error(tmp_path: Path, text: str) -> None:
    assert stored_after(tmp_path, plan_state(text)) == UnreadablePlan()


def test_a_plan_whose_checksum_does_not_match_is_unreadable(tmp_path: Path) -> None:
    plan = a_plan()
    text = plan.to_json()

    stored = stored_after(
        tmp_path, plan_state(text, sha=digest(text + " "), version=plan.method_version)
    )

    assert stored == UnreadablePlan()


@pytest.mark.parametrize(("version", "generated_on"), [(99, "2026-03-02"), (2, "2026-03-03")])
def test_a_plan_whose_columns_disagree_with_its_text_is_unreadable(
    tmp_path: Path, version: int, generated_on: str
) -> None:
    plan = a_plan()

    stored = stored_after(
        tmp_path, plan_state(plan.to_json(), version=version, generated_on=generated_on)
    )

    assert stored == UnreadablePlan()


def test_a_past_day_that_is_tampered_with_is_left_out_not_an_error(tmp_path: Path) -> None:
    plan = a_plan()
    good, tampered, hostile = plan.days[0], plan.days[1], plan.days[2]
    from ponos_coach.planning.domain.serialization import canonical_json  # noqa: PLC0415

    good_text = canonical_json(good)
    hostile_text = json.dumps({**json.loads(canonical_json(hostile)), "load": "x"})
    with closing(migrated(tmp_path)) as raw:
        insert = "INSERT INTO plan_past_day VALUES (?, ?, ?, ?)"
        planned_on = TODAY.isoformat()
        raw.execute(insert, (good.day.isoformat(), planned_on, good_text, digest(good_text)))
        raw.execute(insert, (tampered.day.isoformat(), planned_on, good_text, digest("other")))
        raw.execute(
            insert, (hostile.day.isoformat(), planned_on, hostile_text, digest(hostile_text))
        )
        raw.commit()
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        found = opened.plans.past_days(TODAY, TODAY + timedelta(days=30))
    finally:
        opened.close()

    assert [f.day.day for f in found] == [good.day]
