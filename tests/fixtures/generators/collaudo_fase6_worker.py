# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 6 test process: runs one injury command (or the undo of one) on a profile and dies at the
k-th statement of its transaction.

Usage: `python -I collaudo_fase6_worker.py <folder> <scenario> <kill_at>`. The statements are
counted from the first `BEGIN` or write. At the `kill_at`-th the process ends with `os._exit(77)`,
without closing or rolling back anything, like a power-off. With `kill_at` 0 nothing is killed and
the process prints `statements <n>`; with `kill_at` beyond the last it simply finishes (exit 0).

Scenarios: `open`, `checkin`, `review`, `close`, `remove`, `illness` (the commands) and the same
with the prefix `undo-` (the command runs first, untraced; only its undo is counted and killed).
The data are invented ("Atleta di prova" has no name here: only ids and numbers).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    Onset,
    PainFreeActivity,
    PainReport,
    PainScore,
    Questionnaire,
    Side,
    WarningSign,
)
from ponos_coach.injury.use_cases import (
    CloseProblem,
    ConfirmMedicalReview,
    OpenInjury,
    ProblemChanged,
    ProblemOpened,
    ProblemRemoved,
    RecordCheckIn,
    RemoveProblem,
    ReportIllness,
    RestoreProblem,
)
from ponos_coach.storage.sqlite_profile import SqliteOpenProfile, SqliteProfileStorage

DIED = 77
DAY0 = date(2026, 3, 2)
_FIRST_WRITE = ("BEGIN", "INSERT", "UPDATE", "DELETE", "REPLACE")
FIRST = InjuryId("i1")


def pain(value: int) -> PainScore:
    return PainScore(value)


def questionnaire(notes: str = "", signs: frozenset[WarningSign] = frozenset()) -> Questionnaire:
    return Questionnaire(
        BodyZone.SHIN,
        Side.LEFT,
        Onset.GRADUAL,
        DAY0 - timedelta(days=10),
        PainReport(pain(1), pain(2), pain(4), pain(2)),
        frozenset({PainFreeActivity.CYCLING, PainFreeActivity.SWIMMING}),
        signs,
        notes,
    )


def seed(opened: SqliteOpenProfile) -> None:
    """The template profile: injury `i1` with six check-ins, the last one with a warning sign."""
    OpenInjury(opened.injuries)(Injury(FIRST, DAY0, questionnaire("note of the seed")))
    record = RecordCheckIn(opened.activities, opened.injuries)
    for n in range(1, 6):
        record(FIRST, CheckIn(DAY0 + timedelta(days=n), pain(1), pain(2)))
    record(
        FIRST,
        CheckIn(DAY0 + timedelta(days=6), None, pain(1), frozenset({WarningSign.NIGHT_PAIN})),
    )


def forward(opened: SqliteOpenProfile, scenario: str) -> Callable[[], None]:
    """Runs the command and returns its undo (not yet run)."""
    activities, store = opened.activities, opened.injuries
    new = Injury(InjuryId("n1"), DAY0 + timedelta(days=8), questionnaire("a free note"))
    if scenario == "open":
        opened_result = OpenInjury(store)(new)
        return lambda: _remove(opened, opened_result)
    if scenario == "illness":
        ill = ReportIllness(activities, store)(
            Illness(InjuryId("c1"), IllnessKind.SYSTEMIC, DAY0 + timedelta(days=8))
        )
        return lambda: _remove(opened, ill)
    if scenario == "remove":
        removed = RemoveProblem(activities, store)(FIRST)
        return lambda: RestoreProblem(activities, store)(removed.problem)
    changed: ProblemChanged
    if scenario == "checkin":
        changed = RecordCheckIn(activities, store)(
            FIRST, CheckIn(DAY0 + timedelta(days=7), pain(2), pain(1))
        )
    elif scenario == "review":
        changed = ConfirmMedicalReview(activities, store)(FIRST, DAY0 + timedelta(days=7))
    elif scenario == "close":
        changed = CloseProblem(activities, store)(FIRST, DAY0 + timedelta(days=7))
    else:
        raise SystemExit(f"unknown scenario {scenario}")
    return lambda: RestoreProblem(activities, store)(changed.before)


def _remove(opened: SqliteOpenProfile, result: ProblemOpened | ProblemRemoved) -> None:
    from ponos_coach.injury.domain import problem_id  # noqa: PLC0415

    RemoveProblem(opened.activities, opened.injuries)(problem_id(result.problem))


def main(folder: Path, scenario: str, kill_at: int) -> int:
    opened = SqliteProfileStorage().open(folder)
    assert isinstance(opened, SqliteOpenProfile)
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

    connection = opened._db.connection
    try:
        if scenario == "seed":
            seed(opened)
            return 0
        if scenario.startswith("undo-"):
            undo = forward(opened, scenario.removeprefix("undo-"))
            connection.set_trace_callback(trace)
            undo()
        else:
            connection.set_trace_callback(trace)
            forward(opened, scenario)
    finally:
        connection.set_trace_callback(None)
        opened.close()
    if kill_at == 0:
        sys.stdout.write(f"statements {counter}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])))
