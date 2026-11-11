# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: E501, ANN201
"""T-DATI-02 for the injuries: every command, and the undo that takes it back, is all or nothing
even if the process dies at any statement of its transaction, and the undo is exact.

A worker process (`collaudo_fase6_worker.py`) runs a command on a copy of a real profile and ends
with `os._exit` at the k-th statement. The database left is either exactly how it was or exactly
the finished command. The undo (what each use case returns) gives back the four injury tables
byte for byte, also in-process with random sequences of commands.
"""

from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import date, timedelta
from functools import partial
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    PainScore,
    WarningSign,
    problem_id,
)
from ponos_coach.injury.ports import InjuryStoreError
from ponos_coach.injury.use_cases import (
    CloseProblem,
    ConfirmMedicalReview,
    OpenInjury,
    RecordCheckIn,
    RemoveProblem,
    ReportIllness,
    RestoreProblem,
)
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from tests.collaudo.fase5_support import World, make_world
from tests.fixtures.generators.collaudo_fase6_worker import DAY0, DIED
from tests.injury import builders

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase6_worker.py"
)
TABLES = ("injury", "injury_pain_free", "injury_checkin", "injury_sign")
Dump = dict[str, list[tuple[object, ...]]]
SCENARIOS = ("open", "checkin", "review", "close", "remove", "illness")


def run_worker(profile: Path, scenario: str, kill_at: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-I", str(WORKER), str(profile), scenario, str(kill_at)],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def dump(folder: Path) -> Dump:
    with closing(sqlite3.connect(folder / PROFILE_DATABASE_NAME)) as connection:
        return {
            table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)  # noqa: S608
            for table in TABLES
        }


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A closed profile whose injury `i1` has six check-ins, the last one with a warning sign."""
    base = tmp_path_factory.mktemp("f6-kill") / "p"
    base.mkdir()
    make_world(base).close()
    seeded = run_worker(base / "profilo", "seed", 0)
    assert seeded.returncode == 0, seeded.stderr
    return base


def copy_of(template: Path, destination: Path) -> Path:
    shutil.copytree(template, destination)
    return destination / "profilo"


def statements(folder: Path, scenario: str) -> int:
    done = run_worker(folder, scenario, 0)
    assert done.returncode == 0, done.stderr
    return int(done.stdout.split()[-1])


def kill_points(total: int) -> list[int]:
    return sorted(k for k in {1, 2, 3, total // 2, total - 1, total} if 1 <= k <= total)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_t_dati_02_a_command_killed_at_the_main_statements_leaves_the_old_or_the_new_state(
    template: Path, tmp_path: Path, scenario: str
) -> None:
    done = copy_of(template, tmp_path / "done")
    before = dump(done)
    total = statements(done, scenario)
    after = dump(done)
    assert before != after
    assert total >= 3  # BEGIN, a write, COMMIT

    for kill in kill_points(total):
        folder = copy_of(template, tmp_path / f"k{kill}")
        result = run_worker(folder, scenario, kill)
        assert result.returncode == DIED, result.stderr
        assert dump(folder) == before, f"{scenario} killed at statement {kill} of {total}"
    late = copy_of(template, tmp_path / "late")
    assert run_worker(late, scenario, total + 1).returncode == 0
    assert dump(late) == after


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_t_dati_02_the_undo_killed_at_the_main_statements_is_all_or_nothing_and_exact(
    template: Path, tmp_path: Path, scenario: str
) -> None:
    original = dump(copy_of(template, tmp_path / "plain"))
    forward_only = copy_of(template, tmp_path / "forward")
    assert run_worker(forward_only, scenario, 99).returncode == 0
    changed = dump(forward_only)
    assert changed != original
    count = copy_of(template, tmp_path / "count")
    total = statements(count, f"undo-{scenario}")
    assert dump(count) == original  # the command and its undo leave the tables as they were

    for kill in kill_points(total):
        folder = copy_of(template, tmp_path / f"u{kill}")
        result = run_worker(folder, f"undo-{scenario}", kill)
        assert result.returncode == DIED, result.stderr
        assert dump(folder) == changed, f"{scenario}: undo killed at statement {kill} of {total}"


@pytest.mark.collaudo
@pytest.mark.parametrize("scenario", SCENARIOS)
def test_t_dati_02_a_command_killed_at_every_statement_leaves_the_old_state(
    template: Path, tmp_path: Path, scenario: str
) -> None:
    done = copy_of(template, tmp_path / "done")
    before = dump(done)
    total = statements(done, scenario)
    for kill in range(1, total + 1):
        folder = copy_of(template, tmp_path / f"k{kill}")
        assert run_worker(folder, scenario, kill).returncode == DIED
        assert dump(folder) == before


# --- In-process: random commands and the exact undo ------------------------------------------------
class Live:
    def __init__(self, base: Path) -> None:
        base.mkdir(parents=True)
        self.world: World = make_world(base)
        s = self.world.profile.session
        self.store = s.injuries
        self.open = OpenInjury(s.injuries)
        self.report = ReportIllness(s.activities, s.injuries)
        self.checkin = RecordCheckIn(s.activities, s.injuries)
        self.review = ConfirmMedicalReview(s.activities, s.injuries)
        self.close = CloseProblem(s.activities, s.injuries)
        self.remove = RemoveProblem(s.activities, s.injuries)
        self.restore = RestoreProblem(s.activities, s.injuries)

    def tables(self) -> Dump:
        connection = self.world.profile.session.activities  # noqa: F841 - the file is read below
        with closing(sqlite3.connect(self.world.profile.folder / PROFILE_DATABASE_NAME)) as c:
            return {t: sorted(c.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in TABLES}  # noqa: S608


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory):  # type: ignore[no-untyped-def]
    opened = Live(tmp_path_factory.mktemp("f6-live") / "p")
    yield opened
    opened.world.close()


COMMAND = st.tuples(
    st.sampled_from(["open", "checkin", "review", "close", "remove", "illness"]),
    st.integers(0, 30),
    st.integers(0, 10),
    st.sampled_from([None, WarningSign.NIGHT_PAIN, WarningSign.CHEST_PAIN]),
)


def run_command(
    live: Live, name: str, ident: InjuryId, day: date, value: int, sign: WarningSign | None
) -> Callable[[], object] | None:
    """Runs one random command; returns how to undo it, or None if there was nothing to do."""
    signs = frozenset([sign] if sign else [])
    if name == "open":
        opened = live.open(
            Injury(ident, day, builders.questionnaire(BodyZone.KNEE, signs=sorted(signs, key=str)))
        )
        return partial(live.remove, problem_id(opened.problem))
    if name == "illness":
        reported = live.report(Illness(ident, IllnessKind.NECK_UP, day))
        return partial(live.remove, problem_id(reported.problem))
    injuries = [p for p in live.store.list_problems() if isinstance(p, Injury)]
    if not injuries:
        return None
    target = injuries[0]
    first = max(day, target.opened_on)
    if name == "remove":
        gone = live.remove(target.injury_id)
        return partial(live.restore, gone.problem)
    if name == "checkin":
        changed = live.checkin(
            target.injury_id, CheckIn(first, PainScore(value), PainScore(value), signs)
        )
    elif name == "review":
        changed = live.review(target.injury_id, first)
    else:
        changed = live.close(target.injury_id, max([first, *(c.day for c in target.checkins)]))
    return partial(live.restore, changed.before)


@settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(st.lists(COMMAND, min_size=1, max_size=12))
def test_t_dati_02_undoing_a_random_sequence_gives_back_exactly_the_tables(
    live: Live, script: list[tuple[str, int, int, WarningSign | None]]
) -> None:
    for problem in live.store.list_problems():  # a clean slate for each example
        live.remove(problem_id(problem))
    undo_stack: list[tuple[Dump, Callable[[], object]]] = []
    for serial, (name, offset, value, sign) in enumerate(script, start=1):
        before = live.tables()
        try:
            undo = run_command(
                live, name, InjuryId(f"x{serial}"), DAY0 + timedelta(days=offset), value, sign
            )
        except InjuryStoreError:
            assert live.tables() == before  # a refused command changes nothing
            continue
        if undo is not None:
            undo_stack.append((before, undo))
    while undo_stack:
        before, undo = undo_stack.pop()
        undo()
        assert live.tables() == before
