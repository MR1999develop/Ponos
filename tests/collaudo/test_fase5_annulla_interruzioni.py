# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-02 for goals, availability, session types and the plan: every command, and the undo that
takes it back, is all or nothing even if the process dies at any statement of its transaction.

A worker process runs a command on a copy of a real profile and ends with `os._exit` at the k-th
statement (`collaudo_fase5_worker.py`). The database that is left is either exactly how it was
or exactly the finished command, never something between (a plan without its frozen days, half of
the session types). The undo of every command (the data each use case returns) puts the tables
back exactly, also in-process with random sequences of commands (hypothesis).
"""

from __future__ import annotations

import itertools
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.goals.ports import Goal, GoalStoreError
from ponos_coach.goals.use_cases import (
    AddGoal,
    ChangeGoal,
    RemoveGoal,
    SetAvailability,
    SetSessionChoices,
)
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from tests.collaudo.fase5_support import TODAY, World, make_world, race
from tests.fixtures.generators.collaudo_fase5_worker import DIED

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase5_worker.py"
)
TABLES = ("goal", "weekly_availability", "session_choice", "plan_state", "plan_past_day")
REBUILD_DAY = TODAY + timedelta(days=3)
Dump = dict[str, list[tuple[object, ...]]]
_IDS = itertools.count(1)


@dataclass(frozen=True)
class Command:
    """A command, its undo, and the profile (by name of the template) it starts from."""

    name: str
    forward: tuple[str, str]  # worker scenario and argument
    backward: tuple[str, str] | None
    today: date = TODAY


COMMANDS = (
    Command("change-goal", ("goal-shift", "7:B"), ("goal-shift", "-7:A")),
    Command(
        "remove-goal", ("goal-remove", ""), ("goal-add", (TODAY + timedelta(days=63)).isoformat())
    ),
    Command("session-types", ("choices", "easy"), ("choices", "all")),
    Command("availability", ("availability", "small"), ("availability", "big")),
    Command("rebuild", ("rebuild", ""), None, REBUILD_DAY),
)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A closed profile: six weeks of runs, a goal, availability, a plan saved on TODAY."""
    base = tmp_path_factory.mktemp("f5-kill") / "p"
    base.mkdir()
    built = make_world(base)
    built.import_history(TODAY - timedelta(days=28), TODAY - timedelta(days=1), every=3)
    built.add_goal(race("a", TODAY + timedelta(days=63), Priority.A, 10_000))
    assert isinstance(built.rebuild(TODAY), PlanBuilt)
    # the overview of the day of the rebuild is warm (no cache writes inside the worker)
    built.today = REBUILD_DAY
    built.overview()
    built.close()
    return base


def dump(folder: Path) -> Dump:
    with closing(sqlite3.connect(folder / PROFILE_DATABASE_NAME)) as connection:
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
            for table in TABLES
        }


def run_worker(
    profile: Path, command: tuple[str, str], kill_at: int, today: date
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [
            sys.executable,
            "-I",
            str(WORKER),
            str(profile),
            command[0],
            str(kill_at),
            today.isoformat(),
            command[1],
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def statements_of(profile: Path, command: tuple[str, str], today: date) -> int:
    done = run_worker(profile, command, 0, today)
    assert done.returncode == 0, done.stderr
    return int(done.stdout.split()[-1])


def copy_of(template: Path, destination: Path) -> Path:
    shutil.copytree(template, destination)
    return destination / "profilo"


def kill_points(total: int, *, every: bool) -> list[int]:
    if every:
        return list(range(1, total + 1))
    return sorted(k for k in {1, 2, total // 2, total - 1, total} if 1 <= k <= total)


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c.name)
def test_t_dati_02_a_command_killed_at_the_main_statements_leaves_the_old_or_the_new_state(
    template: Path, tmp_path: Path, command: Command
) -> None:
    done_folder = copy_of(template, tmp_path / "done")
    before = dump(done_folder)
    total = statements_of(done_folder, command.forward, command.today)
    after = dump(done_folder)
    assert before != after, "the command must change something"
    assert total >= 3  # BEGIN, at least one write, COMMIT

    for kill in kill_points(total, every=False):
        folder = copy_of(template, tmp_path / f"k{kill}")
        result = run_worker(folder, command.forward, kill, command.today)
        assert result.returncode == DIED, result.stderr
        assert dump(folder) == before, f"killed at statement {kill} of {total}"
    # killed after the COMMIT statement was issued: the command is finished
    folder = copy_of(template, tmp_path / "late")
    assert run_worker(folder, command.forward, total + 1, command.today).returncode == 0
    assert dump(folder) == after


@pytest.mark.parametrize("command", [c for c in COMMANDS if c.backward], ids=lambda c: c.name)
def test_t_dati_02_the_undo_killed_at_the_main_statements_is_all_or_nothing_and_exact(
    template: Path, tmp_path: Path, command: Command
) -> None:
    assert command.backward is not None
    start = copy_of(template, tmp_path / "start")
    original = dump(start)
    assert run_worker(start, command.forward, 99, command.today).returncode == 0
    changed = dump(start)
    total = statements_of(
        copy_of(start.parent, tmp_path / "count"), command.backward, command.today
    )

    for kill in kill_points(total, every=False):
        folder = copy_of(start.parent, tmp_path / f"u{kill}")
        result = run_worker(folder, command.backward, kill, command.today)
        assert result.returncode == DIED, result.stderr
        assert dump(folder) == changed, f"undo killed at statement {kill} of {total}"
        # the profile still opens, and finishing the undo gives back the original tables
        assert run_worker(folder, command.backward, 99, command.today).returncode == 0
        assert dump(folder) == original


@pytest.mark.collaudo
@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c.name)
def test_t_dati_02_a_command_killed_at_every_statement_leaves_the_old_state(
    template: Path, tmp_path: Path, command: Command
) -> None:
    done_folder = copy_of(template, tmp_path / "done")
    before = dump(done_folder)
    total = statements_of(done_folder, command.forward, command.today)
    for kill in kill_points(total, every=True):
        folder = copy_of(template, tmp_path / f"k{kill}")
        assert run_worker(folder, command.forward, kill, command.today).returncode == DIED
        assert dump(folder) == before


def test_t_dati_02_a_rebuild_that_dies_leaves_no_frozen_day_without_its_plan(
    template: Path, tmp_path: Path
) -> None:
    command = next(c for c in COMMANDS if c.name == "rebuild")
    done = copy_of(template, tmp_path / "done")
    before = dump(done)
    total = statements_of(done, command.forward, command.today)
    after = dump(done)
    assert len(after["plan_past_day"]) == 3  # the three days that became past
    assert before["plan_past_day"] == []
    for kill in range(1, total + 1):
        folder = copy_of(template, tmp_path / f"r{kill}")
        assert run_worker(folder, command.forward, kill, command.today).returncode == DIED
        left = dump(folder)
        assert left in (before, after)
        assert (left["plan_past_day"] == []) == (left["plan_state"] == before["plan_state"])


# --- In-process: random sequences and the exact undo ----------------------------------------------
@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    base = tmp_path_factory.mktemp("f5-live") / "p"
    base.mkdir()
    opened = make_world(base)
    yield opened
    opened.close()


@dataclass(frozen=True)
class Add:
    day: int
    priority: Priority
    distance_m: int


@dataclass(frozen=True)
class Change:
    index: int
    day: int
    priority: Priority
    distance_m: int


@dataclass(frozen=True)
class Remove:
    index: int


@dataclass(frozen=True)
class Types:
    types: frozenset[SessionType]


@dataclass(frozen=True)
class Availability:
    days: frozenset[int]
    sessions: int  # clamped to the days when the value is built
    long_pick: int  # which of the days is the long one
    max_week_min: int


@dataclass(frozen=True)
class ClearAvailability:
    pass


type Operation = Add | Change | Remove | Types | Availability | ClearAvailability

_days = st.integers(1, 700)
_priorities = st.sampled_from(list(Priority))
_distances = st.sampled_from([400, 5_000, 21_097, 500_000])
_operation: st.SearchStrategy[Operation] = st.one_of(
    st.builds(Add, _days, _priorities, _distances),
    st.builds(Change, st.integers(0, 6), _days, _priorities, _distances),
    st.builds(Remove, st.integers(0, 6)),
    st.builds(Types, st.frozensets(st.sampled_from(list(SessionType)), max_size=8)),
    st.builds(
        Availability,
        st.frozensets(st.integers(0, 6), min_size=1),
        st.integers(1, 7),
        st.integers(0, 6),
        st.integers(0, 6000),
    ),
    st.just(ClearAvailability()),
)


def table_state(world: World) -> tuple[object, ...]:
    session = world.profile.session
    choices = session.session_choices.get()
    return (
        tuple(session.goals.list_goals()),
        session.availability.get(),
        tuple(sorted((sport.value, t.value) for sport in Sport for t in choices.for_sport(sport))),
    )


def apply(world: World, operation: Operation) -> Callable[[], object] | None:  # noqa: PLR0911 - one case each
    """Runs one command and returns what takes it back, as the interface keeps it for undo.
    None: the command did nothing (no goal to change) or was refused."""
    session = world.profile.session
    goals = list(session.goals.list_goals())
    try:
        match operation:
            case Add(day, priority, distance_m):
                goal = RaceGoal(
                    GoalId(f"g{next(_IDS)}"),
                    TODAY + timedelta(days=day),
                    priority,
                    Sport.RUNNING,
                    distance_m,
                    None,
                )
                added = AddGoal(session.goals)(goal)
                return lambda: RemoveGoal(session.activities, session.goals)(added.goal.goal_id)
            case Change(index, day, priority, distance_m) if goals:
                target = goals[index % len(goals)]
                if not isinstance(target, RaceGoal):
                    return None
                new = replace(
                    target,
                    day=TODAY + timedelta(days=day),
                    priority=priority,
                    distance_m=distance_m,
                )
                changed = ChangeGoal(session.activities, session.goals)(new)
                return lambda: ChangeGoal(session.activities, session.goals)(changed.before)
            case Remove(index) if goals:
                removed = RemoveGoal(session.activities, session.goals)(
                    goals[index % len(goals)].goal_id
                )
                return lambda: AddGoal(session.goals)(removed.goal)
            case Types(types):
                switched = SetSessionChoices(session.activities, session.session_choices)(
                    SessionChoices.of({Sport.RUNNING: types})
                )
                return lambda: SetSessionChoices(session.activities, session.session_choices)(
                    switched.before
                )
            case Availability(days, sessions, long_pick, max_week_min):
                ordered = sorted(days)
                set_to = SetAvailability(session.activities, session.availability)(
                    WeeklyAvailability(
                        days,
                        min(sessions, len(days)),
                        ordered[long_pick % len(ordered)],
                        max_week_min,
                    )
                )
                return lambda: SetAvailability(session.activities, session.availability)(
                    set_to.before
                )
            case ClearAvailability():
                cleared = SetAvailability(session.activities, session.availability)(None)
                return lambda: SetAvailability(session.activities, session.availability)(
                    cleared.before
                )
    except GoalStoreError:
        return None  # refused (a second race on a day): nothing must have changed
    return None


@given(st.lists(_operation, min_size=1, max_size=8))
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
def test_t_dati_02_undoing_a_random_sequence_of_commands_gives_back_exactly_the_tables(
    live: World, operations: list[Operation]
) -> None:
    start = table_state(live)
    undo: list[Callable[[], object]] = []
    for operation in operations:
        before = table_state(live)
        taken_back = apply(live, operation)
        if taken_back is None:
            assert table_state(live) == before  # a refused or empty command changes nothing
        else:
            undo.append(taken_back)
    for take_back in reversed(undo):
        take_back()
    assert table_state(live) == start


def test_a_refused_command_changes_nothing(live: World) -> None:
    session = live.profile.session
    day = TODAY + timedelta(days=500)
    first = RaceGoal(GoalId("x1"), day, Priority.A, Sport.RUNNING, 5_000, None)
    AddGoal(session.goals)(first)
    state = table_state(live)
    other: Goal = replace(first, goal_id=GoalId("x2"))
    with pytest.raises(GoalStoreError):
        AddGoal(session.goals)(other)  # a second race on the same day
    with pytest.raises(GoalStoreError):
        AddGoal(session.goals)(first)  # same id
    assert table_state(live) == state
    RemoveGoal(session.activities, session.goals)(first.goal_id)
