# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 5 test process: runs one command on a profile and dies at the k-th statement of its
transaction.

Usage: `python -I collaudo_fase5_worker.py <folder> <scenario> <kill_at> <today> [argument]`.
The statements are counted from the first `BEGIN` or write (the reads of the overview before
it do not count). At the `kill_at`-th the process ends with `os._exit(77)`, without closing or
rolling back anything: the death of the process in the middle of a command, like a power-off. With
`kill_at` 0 nothing is killed and the process prints `statements <n>` (how many the command ran);
with `kill_at` beyond the last it simply finishes (exit 0).

Scenarios (the argument is a goal id, a date or a number as the scenario says):
`goal-shift <days>:<priority>`, `goal-remove`, `goal-add <date>`, `choices <easy|all>`,
`availability <small|big>`, `rebuild`.
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.goals.use_cases import (
    AddGoal,
    ChangeGoal,
    RemoveGoal,
    SetAvailability,
    SetSessionChoices,
)
from ponos_coach.load.use_cases import ComputeProfileOverview
from ponos_coach.profiles.ports import OpenProfile
from ponos_coach.profiles.rebuild_plan import PlanSources, RebuildPlan
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteOpenProfile, SqliteProfileStorage

DIED = 77
_FIRST_WRITE = ("BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE")
GOAL = GoalId("a")


def main(folder: Path, scenario: str, kill_at: int, today: date, argument: str) -> int:
    opened = SqliteProfileStorage().open(folder)
    counter = 0
    armed = False

    def trace(statement: str) -> None:
        nonlocal counter, armed
        armed = armed or statement.lstrip().upper().startswith(_FIRST_WRITE)
        if not armed:
            return
        counter += 1
        if counter == kill_at:
            os._exit(DIED)

    assert isinstance(opened, SqliteOpenProfile)
    connection = opened._db.connection
    connection.set_trace_callback(trace)
    try:
        _run(opened, folder, scenario, today, argument)
    finally:
        connection.set_trace_callback(None)
        opened.close()
    if kill_at == 0:
        sys.stdout.write(f"statements {counter}\n")
    return 0


def _run(opened: OpenProfile, folder: Path, scenario: str, today: date, argument: str) -> None:
    store = opened  # the stores are attributes of the open profile
    activities = store.activities
    goals = store.goals
    if scenario == "goal-shift":
        days, priority = argument.split(":")
        current = goals.get(GOAL)
        assert isinstance(current, RaceGoal)
        ChangeGoal(activities, goals)(
            replace(
                current, day=current.day + timedelta(days=int(days)), priority=Priority(priority)
            )
        )
    elif scenario == "goal-remove":
        RemoveGoal(activities, goals)(GOAL)
    elif scenario == "goal-add":
        AddGoal(goals)(
            RaceGoal(GOAL, date.fromisoformat(argument), Priority.A, Sport.RUNNING, 10_000, None)
        )
    elif scenario == "choices":
        types = {SessionType.EASY} if argument == "easy" else set(SessionType)
        SetSessionChoices(activities, store.session_choices)(
            SessionChoices.of({Sport.RUNNING: types})
        )
    elif scenario == "availability":
        small = argument == "small"
        SetAvailability(activities, store.availability)(
            WeeklyAvailability(frozenset(range(7)), 3 if small else 7, 6, 210 if small else 1200)
        )
    elif scenario == "rebuild":
        sources = PlanSources(
            activities,
            goals,
            store.availability,
            store.session_choices,
            store.physiology,
            store.sport_weights,
            store.threshold_tests,
            store.plans,
            store.injuries,
        )
        overview = ComputeProfileOverview(
            activities,
            FileArchive.in_profile_folder(folder),
            store.physiology,
            store.load_cache,
            decode_stored_activity,
            sport_weights=store.sport_weights,
            timezone=ZoneInfo("Europe/Rome"),
            today=lambda: today,
        )
        RebuildPlan(sources, overview.execute)(today)
    else:
        raise SystemExit(f"unknown scenario {scenario}")


if __name__ == "__main__":
    sys.exit(
        main(
            Path(sys.argv[1]),
            sys.argv[2],
            int(sys.argv[3]),
            date.fromisoformat(sys.argv[4]),
            sys.argv[5] if len(sys.argv) > 5 else "",
        )
    )
