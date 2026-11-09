# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders and gestures shared by the goals, training settings and plan window tests.

The profile and the activities are the made-up ones of `nutrition_support` (today is 30 June 2026).
Gestures go through the dialogs, as a person would, and wait for the data thread.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import RaceGoal, SessionChoices, SessionType, WeeklyAvailability
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import Plan
from ponos_coach.ui.birth_date_edit import to_qdate
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.plan_presentation import PlanAvailable, PlanRefusedNow
from tests.ui.nutrition_support import cell_of, window_with_days
from tests.ui.workspace_support import TODAY, WAIT_MS

RACE_DAY = TODAY + timedelta(weeks=12)  # 22 September 2026
EVERY_DAY = frozenset(range(7))


def ready_window(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    """A profile with activities, and the plan's choices read."""
    window = window_with_days(qtbot, tmp_path, settings)
    wait_idle(qtbot, window)
    return window


def wait_idle(qtbot: QtBot, window: MainWindow) -> None:
    """Wait until the choices are read and no change is on its way to the store."""
    qtbot.waitUntil(
        lambda: window.plan.snapshot is not None and window.plan.is_idle, timeout=WAIT_MS
    )


def goals_of(window: MainWindow) -> tuple[Goal, ...]:
    snapshot = window.plan.snapshot
    assert snapshot is not None
    return snapshot.goals


def races_of(window: MainWindow) -> list[RaceGoal]:
    return [g for g in goals_of(window) if isinstance(g, RaceGoal)]


def wait_for(qtbot: QtBot, condition: Callable[[], bool]) -> None:
    qtbot.waitUntil(condition, timeout=WAIT_MS)


def select_goal(qtbot: QtBot, window: MainWindow, goal_id: str) -> None:
    """Select in the calendar the day of a goal (what the goal commands act on)."""
    goal = next(g for g in goals_of(window) if g.goal_id.value == goal_id)
    window.calendar_page.go_to(goal.day)
    wait_for(qtbot, lambda: bool(cell_of(window, goal.day).goals))


def add_race(
    qtbot: QtBot,
    window: MainWindow,
    day: date = RACE_DAY,
    *,
    target: str = "",
    priority_index: int | None = None,
    distance_index: int | None = None,
) -> None:
    """Add a race through the dialog (menu command), and wait until it is stored."""
    wait_idle(qtbot, window)
    before = len(goals_of(window))
    window.plan.actions["add_race"].trigger()
    wait_for(qtbot, lambda: window.plan.goal_dialog is not None)
    dialog = window.plan.goal_dialog
    assert dialog is not None
    dialog.date_edit.setDate(to_qdate(day))
    if target:
        dialog.target_time_edit.setText(target)
    if priority_index is not None:
        dialog.priority_combo.setCurrentIndex(priority_index)
    if distance_index is not None:
        dialog.distance_combo.setCurrentIndex(distance_index)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and len(goals_of(window)) == before + 1)


def set_training(
    qtbot: QtBot,
    window: MainWindow,
    *,
    days: frozenset[int] = EVERY_DAY,
    sessions: int | None = None,
    long_day: int = 6,
    hours: float = 12.0,
    types: frozenset[SessionType] = frozenset(SessionType),
    sport: Sport = Sport.RUNNING,
) -> None:
    """Set the availability and the session types of a sport through the dialog."""
    wait_idle(qtbot, window)
    window.plan.actions["training"].trigger()
    wait_for(qtbot, lambda: window.plan.training_dialog is not None)
    dialog = window.plan.training_dialog
    assert dialog is not None
    for weekday, check in enumerate(dialog.day_checks):
        check.setChecked(weekday in days)
    count = len(days) if sessions is None else sessions
    dialog.sessions_spin.setValue(count)
    dialog.long_day_combo.setCurrentIndex(dialog.long_day_combo.findData(long_day))
    dialog.week_hours_spin.setValue(hours)
    for (check_sport, session_type), check in dialog.type_checks.items():
        check.setChecked(check_sport is sport and session_type in types)
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.plan.is_idle and window.plan.snapshot is not None)
    wait_for(
        qtbot,
        lambda: (
            _availability(window) == WeeklyAvailability(days, count, long_day, round(hours * 60))
        ),
    )


def _availability(window: MainWindow) -> WeeklyAvailability | None:
    snapshot = window.plan.snapshot
    return None if snapshot is None else snapshot.availability


def choices_of(window: MainWindow) -> SessionChoices:
    snapshot = window.plan.snapshot
    assert snapshot is not None
    return snapshot.choices


def wait_plan(qtbot: QtBot, window: MainWindow, race_ids: frozenset[str]) -> Plan:
    """Wait until the plan shown is built for exactly these races."""

    def built() -> bool:
        state = window.plan.state
        return isinstance(state, PlanAvailable) and {
            r.goal_id.value for r in state.plan.inputs.races
        } == set(race_ids)

    wait_for(qtbot, lambda: window.plan.is_idle and built())
    state = window.plan.state
    assert isinstance(state, PlanAvailable)
    return state.plan


def wait_refusal(qtbot: QtBot, window: MainWindow, text: str) -> None:
    """Wait until the Goals section says there is no plan, with a sentence containing `text`."""

    def shown() -> bool:
        return isinstance(window.plan.state, PlanRefusedNow) and any(
            text in shown_text for shown_text in window.goals_page.notice_texts
        )

    wait_for(qtbot, lambda: window.plan.is_idle and shown())


def race_ids(window: MainWindow) -> frozenset[str]:
    return frozenset(r.goal_id.value for r in races_of(window))
