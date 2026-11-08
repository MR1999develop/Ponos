# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-02 for the saved plan: the plan and the frozen days come out of a file the user can edit,
so they are input. Checksum, JSON and content are checked; what fails is "unreadable", never an
exception, and the next rebuild writes a good plan.

Two levels: the real database edited by hand (checksum kept, checksum recomputed over hostile
content), and a mutation fuzzer on the decoder (hypothesis) whose oracle goes beyond "does not
crash": what is accepted writes and reads back to the same text.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.goals.domain import Priority
from ponos_coach.planning.domain import Plan, build_plan
from ponos_coach.planning.plan_codec import (
    MAX_PLAN_JSON_CHARS,
    PlanDecodingError,
    decode_plan,
    digest,
)
from ponos_coach.profiles.day_nutrition import NutritionUnavailable, UnavailableReason
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from tests.collaudo.fase5_support import TODAY, World, make_world, race, reopen_world
from tests.planning.builders import inputs
from tests.planning.builders import race as pure_race

type Mutation = Callable[[dict[str, object]], object]


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    opened = make_world(tmp_path / "w")
    opened.import_history(TODAY - timedelta(days=14), TODAY - timedelta(days=1), every=3)
    opened.add_goal(race("a", TODAY + timedelta(days=60), Priority.A, 10_000))
    opened.built()
    yield opened
    opened.close()


def edit_database(world: World, action: Callable[[sqlite3.Connection], None]) -> World:
    """Closes the profile, edits the file by hand, opens it again."""
    world.close()
    with closing(sqlite3.connect(world.base / "profilo" / PROFILE_DATABASE_NAME)) as connection:
        action(connection)
        connection.commit()
    return reopen_world(world.base, today=world.today)


def saved_text(world: World) -> str:
    world.close()
    with closing(sqlite3.connect(world.base / "profilo" / PROFILE_DATABASE_NAME)) as connection:
        row = connection.execute("SELECT plan_json FROM plan_state").fetchone()
    return str(row[0])


# --- Mutations of the plan tree ------------------------------------------------------------------
def _first_day(tree: dict[str, object]) -> dict[str, object]:
    days = tree["days"]
    assert isinstance(days, list)
    return days[len(days) // 2]  # type: ignore[no-any-return]


def _set(tree: dict[str, object], key: str, value: object) -> None:
    tree[key] = value


MUTATIONS: dict[str, Mutation] = {
    "extra-field": lambda t: _set(t, "surprise", 1),
    "missing-field": lambda t: t.pop("projection"),
    "unknown-version-type": lambda t: _set(t, "method_version", "three"),
    "date-with-no-zero": lambda t: _set(t, "generated_on", "2026-3-2"),
    "date-impossible": lambda t: _set(t, "generated_on", "2026-02-30"),
    "wrong-tag": lambda t: _set(t, "_type", "Block"),
    "days-not-a-list": lambda t: _set(t, "days", {"x": 1}),
    "unknown-phase": lambda t: _first_day(t).update({"phase": "sprint"}),
    "bool-for-int": lambda t: _set(t, "method_version", True),
    "float-for-int": lambda t: _set(t, "method_version", 3.0),
    "infinite-load": lambda t: _first_day(t).update({"load": float("inf")}),
    "none-everywhere": lambda t: _set(t, "weeks", None),
    "huge-integer": lambda t: _set(t, "method_version", 10**400),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_t_sec_02_hostile_content_with_a_good_checksum_is_unreadable_and_the_next_rebuild_heals(
    world: World, name: str
) -> None:
    tree = json.loads(saved_text(world))
    MUTATIONS[name](tree)
    text = json.dumps(tree, sort_keys=True, separators=(",", ":"))

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE plan_state SET plan_json = ?, plan_sha256 = ?", (text, digest(text))
        )

    tampered = edit_database(world, hand_edit)
    try:
        assert tampered.current() is None  # unreadable is "no plan", not an exception
        future = tampered.nutrition(TODAY + timedelta(days=3))
        assert future == NutritionUnavailable(UnavailableReason.NO_PLAN_FOR_DAY)
        healed = tampered.rebuild(TODAY)
        assert isinstance(healed, PlanBuilt)
        assert healed.saved
        assert tampered.saved_plan().to_json() == healed.plan.to_json()
    finally:
        tampered.close()


def _session_day(tree: dict[str, object]) -> dict[str, object]:
    days = tree["days"]
    assert isinstance(days, list)
    return next(d for d in days if d["session"] is not None)


def _race_day(tree: dict[str, object]) -> dict[str, object]:
    days = tree["days"]
    assert isinstance(days, list)
    return next(d for d in days if d["race"] is not None)


@pytest.mark.parametrize(
    ("where", "field", "value"),
    [
        ("session", "load", -5.0),
        ("session", "minutes", 0),
        ("session", "minutes", -30),
        ("race", "expected_time_s", 0),
        ("race", "load", -1.0),
    ],
)
def test_t_sec_02_a_checksummed_plan_with_impossible_numbers_never_breaks_the_nutrition(
    world: World, where: str, field: str, value: float
) -> None:
    tree = json.loads(saved_text(world))
    day = _session_day(tree) if where == "session" else _race_day(tree)
    day[where][field] = value  # type: ignore[index]
    text = json.dumps(tree, sort_keys=True, separators=(",", ":"))

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE plan_state SET plan_json = ?, plan_sha256 = ?", (text, digest(text))
        )

    tampered = edit_database(world, hand_edit)
    try:
        if tampered.current() is not None:  # read back: then it must be usable
            tampered.nutrition(date.fromisoformat(str(day["day"])))
    finally:
        tampered.close()


@pytest.mark.parametrize(
    "damage",
    [
        lambda text: text[:-7],
        lambda text: text.replace("{", "[", 1),
        lambda text: text.replace('"days"', '"dayz"', 1),
        lambda text: text + " ",
        lambda _text: "[" * 100_000,
        lambda text: text.replace('"ctl":', '"ctl":NaN,"x":', 1),
        lambda _text: "",
        lambda text: "﻿" + text,
    ],
)
def test_t_sec_02_text_damaged_with_the_old_checksum_is_unreadable_and_heals(
    world: World, damage: Callable[[str], str]
) -> None:
    original = saved_text(world)
    damaged = damage(original)

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE plan_state SET plan_json = ?", (damaged if len(damaged) >= 2 else "  ",)
        )

    tampered = edit_database(world, hand_edit)
    try:
        assert tampered.current() is None
        assert isinstance(tampered.rebuild(TODAY), PlanBuilt)
        assert tampered.saved_plan().to_json() == original
    finally:
        tampered.close()


def test_a_plan_at_exactly_the_size_cap_with_a_good_checksum_is_read_not_trusted(
    world: World,
) -> None:
    padding = " " * (MAX_PLAN_JSON_CHARS - 2)
    text = "{" + padding + "}"

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE plan_state SET plan_json = ?, plan_sha256 = ?", (text, digest(text))
        )

    tampered = edit_database(world, hand_edit)
    try:
        assert tampered.current() is None  # a valid JSON object that is not a plan
    finally:
        tampered.close()


def test_a_frozen_day_with_a_wrong_checksum_is_left_out_and_the_adherence_goes_on(
    world: World,
) -> None:
    world.today = TODAY + timedelta(days=4)
    assert isinstance(world.rebuild(world.today), PlanBuilt)
    assert len(world.past(TODAY, world.today)) == 4
    target = (TODAY + timedelta(days=2)).isoformat()

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER plan_past_day_is_fixed")  # the owner can; the app cannot
        connection.execute(
            "UPDATE plan_past_day SET day_json = replace(day_json, '\"load\"', '\"lod\"') "
            "WHERE day = ?",
            (target,),
        )

    tampered = edit_database(world, hand_edit)
    try:
        tampered.today = world.today
        days = [f.day.day for f in tampered.past(TODAY, tampered.today)]
        assert days == [TODAY + timedelta(days=n) for n in (1, 3, 4)]
        adherence = tampered.adherence(TODAY, tampered.today)
        assert [a.day for a in adherence] == days
    finally:
        tampered.close()


def test_a_frozen_day_with_hostile_content_and_a_good_checksum_is_left_out(world: World) -> None:
    world.today = TODAY + timedelta(days=2)
    assert isinstance(world.rebuild(world.today), PlanBuilt)

    def hand_edit(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TRIGGER plan_past_day_is_fixed")
        text = '{"_type":"PlanDay","day":"2026-03-03","phase":"base","load":-1,"race":null}'
        connection.execute(
            "UPDATE plan_past_day SET day_json = ?, day_sha256 = ? WHERE day = '2026-03-03'",
            (text, digest(text)),
        )

    tampered = edit_database(world, hand_edit)
    try:
        assert [f.day.day for f in tampered.past(TODAY, TODAY + timedelta(days=9))] == [
            TODAY + timedelta(days=2)
        ]
    finally:
        tampered.close()


# --- The decoder under mutation -------------------------------------------------------------------
PURE_PLAN = build_plan(
    inputs([pure_race("a", date(2026, 5, 3), Priority.A, 21_097)]), date(2026, 3, 2)
)
assert isinstance(PURE_PLAN, Plan)
PURE_TEXT = PURE_PLAN.to_json()
PURE_TREE = json.loads(PURE_TEXT)

_junk = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**12), 10**12),
    st.floats(allow_nan=False, allow_infinity=True),
    st.text(max_size=12),
    st.just([]),
    st.just({}),
    st.lists(st.integers(), max_size=3),
    st.sampled_from(["2026-13-01", "0000-00-00", "2026-03-02T00:00", "base", "x" * 200]),
)


def _paths(node: object, prefix: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    found: list[tuple[object, ...]] = [prefix]
    if isinstance(node, dict):
        for key, child in node.items():
            found += _paths(child, (*prefix, key))
    elif isinstance(node, list):
        for index in range(0, len(node), max(1, len(node) // 40)):
            found += _paths(node[index], (*prefix, index))
    return found


PATHS = _paths(PURE_TREE)


def _mutated(path: tuple[object, ...], junk: object) -> str:
    tree = json.loads(PURE_TEXT)
    if not path:
        return json.dumps(junk)
    parent: object = tree
    for step in path[:-1]:
        parent = parent[step]  # type: ignore[index]
    parent[path[-1]] = junk  # type: ignore[index]
    return json.dumps(tree, sort_keys=True, separators=(",", ":"))


def check_decoder(path: tuple[object, ...], junk: object) -> None:
    text = _mutated(path, junk)
    try:
        plan = decode_plan(text, digest(text))
    except PlanDecodingError as error:
        assert error.__cause__ is None  # the hostile text is not kept in the chain
        assert text[:60] not in str(error)
        return
    again = plan.to_json()  # accepted: it writes and reads back to the same text
    assert decode_plan(again, digest(again)).to_json() == again


@given(st.sampled_from(PATHS), _junk)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_sec_02_a_mutated_plan_is_rejected_or_stable(
    path: tuple[object, ...], junk: object
) -> None:
    check_decoder(path, junk)


@pytest.mark.collaudo
@given(st.sampled_from(PATHS), _junk)
@settings(max_examples=4000, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_t_sec_02_a_mutated_plan_is_rejected_or_stable_long(
    path: tuple[object, ...], junk: object
) -> None:
    check_decoder(path, junk)
