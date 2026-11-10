# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Where a race is described the elevation gain is shown: the goals table (its own column, a
number sorted as a number), the phrase of the goal (calendar, notices, menus) and the planned
sessions on hills (their name and an indicative climb)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from PySide6.QtCore import QLocale, Qt
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import FitnessGoal, GoalId, Priority, RaceGoal, SessionType
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import Block, BlockRole, PlannedSession
from ponos_coach.ui.goals_page import GoalsPage
from ponos_coach.ui.goals_table import SORT_ROLE, GoalColumn
from ponos_coach.ui.plan_texts import goal_label, label_lookup, session_lines, session_title
from ponos_coach.ui.tokens import Theme

TRAIL = RaceGoal(GoalId("t"), date(2026, 10, 18), Priority.A, Sport.TRAIL, 30_000, None, 1_500)
FLAT = RaceGoal(GoalId("f"), date(2026, 8, 30), Priority.B, Sport.RUNNING, 10_000, None)
FORM = FitnessGoal(GoalId("g"), date(2026, 9, 20), 60.0)


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def show(page: GoalsPage, goals: list[Goal]) -> None:
    lookup = label_lookup({g.goal_id.value: g for g in goals})
    page.show_state((), goals, {}, lookup, Theme.LIGHT)


def test_the_goal_phrase_says_the_climb_only_when_there_is_one() -> None:
    assert goal_label(TRAIL) == "Race A, 30 km, +1,500 m, 10/18/26"
    assert goal_label(FLAT) == "Race B, 10 km, 8/30/26"


def test_the_table_has_a_column_of_elevation_gain_that_sorts_as_a_number(qtbot: QtBot) -> None:
    page = GoalsPage()
    qtbot.addWidget(page)
    show(page, [TRAIL, FLAT, FORM])

    header = page.proxy.headerData(GoalColumn.ASCENT, Qt.Orientation.Horizontal)
    assert header == "Elevation gain (m)"
    cells = {
        str(page.proxy.index(r, GoalColumn.KIND).data()): str(
            page.proxy.index(r, GoalColumn.ASCENT).data()
        )
        for r in range(3)
    }
    assert cells == {"Race A": "1,500", "Race B": "", "Form goal": ""}
    keys = sorted(float(page.model.index(r, GoalColumn.ASCENT).data(SORT_ROLE)) for r in range(3))
    assert keys == [-1.0, 0.0, 1_500.0]


def a_session(session_type: SessionType, ascent_m: int) -> PlannedSession:
    block = Block(BlockRole.STEADY, 60, 2, 1, 120, 140, None, None)
    return PlannedSession(Sport.RUNNING, session_type, 60, (block,), 50.0, ascent_m=ascent_m)


def test_hill_sessions_are_named_and_say_their_indicative_climb() -> None:
    repeats = a_session(SessionType.VO2MAX, 120)
    hilly_long = a_session(SessionType.LONG, 420)
    plain_long = a_session(SessionType.LONG, 0)

    assert session_title(repeats) == "Hill repeats, 60 min"
    assert session_title(hilly_long) == "Hilly long run, 60 min"
    assert session_title(plain_long) == "Long, 60 min"
    assert "Elevation gain: about 420 m" in session_lines(hilly_long)
    assert not any("Elevation gain" in line for line in session_lines(plain_long))
