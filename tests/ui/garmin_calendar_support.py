# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Window with a connected account, a plan and a fake Garmin calendar, for the calendar tests.

Everything is a fake of `tests/fakes/`: no network, no real login. Names and data are made up.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import SessionTokens, Sport
from ponos_coach.activities.ports import GarminSession
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal, SessionChoices, SessionType
from ponos_coach.goals.use_cases import AddGoal, SetAvailability, SetSessionChoices
from ponos_coach.planning.domain import Plan
from ponos_coach.planning.domain.workouts import ScheduledWorkout, WorkoutSpec
from ponos_coach.planning.workout_calendar import WorkoutCalendar
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from ponos_coach.ui.data_thread import JobSucceeded
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.plan_presentation import plan_of
from ponos_coach.ui.sync_model import AccountStatus
from ponos_coach.ui.workspace import ProfileAccess
from tests.fakes.garmin import FakeActivitySource, FakeGarminAccount, make_remote_history
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.tokens import InMemoryTokenStore
from tests.fakes.workout_calendar import InMemoryWorkoutCalendar
from tests.planning.builders import BIG_WEEK
from tests.ui.garmin_support import PROFILE_NAME, link_account, wait_account, wait_activities
from tests.ui.workspace_support import (
    TODAY,
    WAIT_MS,
    GarminFakes,
    compose,
    create_profile,
)


@dataclass(frozen=True, slots=True)
class SessionWithCalendar(GarminSession):
    """A session of an adapter that also manages workouts (`None`: it cannot)."""

    calendar: WorkoutCalendar | None = None


class CalendarAccount(FakeGarminAccount):
    """The fake account, whose sessions carry a workout calendar."""

    def __init__(self, source: FakeActivitySource, calendar: WorkoutCalendar | None) -> None:
        super().__init__(source, mfa_required=False)
        self._calendar = calendar

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        session = super().login(email, password, prompt_mfa_code)
        return SessionWithCalendar(session.source, session.tokens, self._calendar)

    def resume(self, tokens: SessionTokens) -> GarminSession:
        session = super().resume(tokens)
        return SessionWithCalendar(session.source, session.tokens, self._calendar)


class GatedCalendar(InMemoryWorkoutCalendar):
    """Each creation waits at a gate, with a deadline; `entered` fires when one has arrived."""

    def __init__(self, gate: threading.Event) -> None:
        super().__init__()
        self._gate = gate
        self.entered = threading.Event()

    def create_and_schedule(self, spec: WorkoutSpec, day: date) -> ScheduledWorkout:
        self.entered.set()
        if not self._gate.wait(timeout=30):
            raise TimeoutError("gate never opened")
        return super().create_and_schedule(spec, day)


def make_calendar_fakes(calendar: WorkoutCalendar | None) -> GarminFakes:
    """Fake Garmin with two runs, whose account gives sessions with this calendar."""
    source = FakeActivitySource(make_remote_history(2))
    return GarminFakes(
        source, CalendarAccount(source, calendar), InMemoryTokenStore(), RecordingNotifier()
    )


def unlinked_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, calendar: WorkoutCalendar | None
) -> MainWindow:
    """Window with a new profile and no account."""
    window = compose(tmp_path, settings, fakes=make_calendar_fakes(calendar))
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)
    wait_account(qtbot, window, AccountStatus.NOT_LINKED)
    return window


def window_with_plan(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, calendar: WorkoutCalendar | None
) -> MainWindow:
    """Window with the account connected, goals and a plan (the first one the engine builds)."""
    window = unlinked_window(qtbot, tmp_path, settings, calendar)
    link_account(qtbot, window, mfa=False)
    wait_activities(qtbot, window, 2)
    outcomes: list[object] = []

    def set_up(access: ProfileAccess) -> None:
        session = access.session
        goal = RaceGoal(
            GoalId("marathon"), TODAY + timedelta(weeks=12), Priority.A, Sport.RUNNING, 42_195, None
        )
        AddGoal(session.goals)(goal)
        SetAvailability(session.activities, session.availability)(BIG_WEEK)
        SetSessionChoices(session.activities, session.session_choices)(
            SessionChoices.of({Sport.RUNNING: frozenset(SessionType)})
        )

    assert window.controller.run_on_profile(set_up, outcomes.append)
    qtbot.waitUntil(lambda: bool(outcomes), timeout=WAIT_MS)
    assert isinstance(outcomes[0], JobSucceeded)
    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as rebuilt:
        assert window.controller.recalculate_overview()
    assert isinstance(rebuilt.args[0], PlanBuilt)
    qtbot.waitUntil(lambda: plan_of(window.plan.state) is not None, timeout=WAIT_MS)
    return window


def current_plan(window: MainWindow) -> Plan:
    """The plan the window shows."""
    plan = plan_of(window.plan.state)
    assert plan is not None
    return plan


def session_days(plan: Plan, today: date = TODAY) -> list[date]:
    """The days from `today` on with a planned session."""
    return [item.day for item in plan.days if item.session is not None and item.day >= today]


def select_day(window: MainWindow, day: date) -> None:
    """Select a day in the calendar page, moving to its month first."""
    page = window.calendar_page
    while page.current_month != day.replace(day=1):
        page.next_month()
    page.select_day(day)
    assert page.selected_day() == day


def wait_job(qtbot: QtBot, window: MainWindow, start: Callable[[], object]) -> object:
    """Run `start` and wait for the end of the calendar job it begins; return its result."""
    with qtbot.waitSignal(window.controller.sync.calendar_job_finished, timeout=WAIT_MS) as done:
        start()
    return done.args[0]


def summary_box(qtbot: QtBot, window: MainWindow) -> QMessageBox:
    """Wait for the calendar summary and return it."""

    def find() -> QMessageBox | None:
        return next(
            (
                box
                for box in window.open_boxes
                if isinstance(box, QMessageBox) and box.objectName() == "calendarSummary"
            ),
            None,
        )

    qtbot.waitUntil(lambda: find() is not None, timeout=WAIT_MS)
    box = find()
    assert box is not None
    return box
