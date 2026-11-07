# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The plan follows every overview: after a synchronisation or an import it is rebuilt on the data
thread, never blocks the window, and a failure of the plan never fails the synchronisation.

Garmin is the fake of `tests/fakes/` (no network, no real login).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal, SessionChoices, SessionType
from ponos_coach.goals.use_cases import AddGoal, SetAvailability, SetSessionChoices
from ponos_coach.planning.domain import Plan
from ponos_coach.planning.use_cases import CurrentPlan
from ponos_coach.profiles.rebuild_plan import PlanBuilt, PlanRefused, RebuildPlan
from ponos_coach.ui.data_thread import JobOutcome, JobSucceeded
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.sync_model import AccountStatus
from ponos_coach.ui.workspace import ProfileAccess
from tests.fakes.garmin import FakeActivitySource, make_remote_history
from tests.planning.builders import BIG_WEEK
from tests.ui.garmin_support import (
    PROFILE_NAME,
    link_account,
    remote_runs,
    sync_and_wait,
    wait_account,
    wait_activities,
)
from tests.ui.workspace_support import (
    TODAY,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    fakes_of,
    make_fakes,
    make_import_folder,
    make_settings,
)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def linked_window(qtbot: QtBot, tmp_path: Path, settings: QSettings, runs: int) -> MainWindow:
    fakes = make_fakes(mfa_required=False, source=FakeActivitySource(make_remote_history(runs)))
    window = compose(tmp_path, settings, fakes=fakes)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)
    wait_account(qtbot, window, AccountStatus.NOT_LINKED)
    link_account(qtbot, window, mfa=False)
    wait_activities(qtbot, window, runs)
    return window


def run_on_data_thread(qtbot: QtBot, window: MainWindow, work: object) -> JobOutcome:
    """Run `work(access)` on the profile and wait for its outcome."""
    outcomes: list[JobOutcome] = []
    assert callable(work)
    assert window.controller.run_on_profile(work, outcomes.append)
    qtbot.waitUntil(lambda: bool(outcomes), timeout=WAIT_MS)
    return outcomes[0]


def set_up_goals(qtbot: QtBot, window: MainWindow) -> None:
    """A marathon in twelve weeks, a free week and every type of session for running."""

    def work(access: ProfileAccess) -> None:
        session = access.session
        goal = RaceGoal(
            GoalId("marathon"), TODAY + timedelta(weeks=12), Priority.A, Sport.RUNNING, 42_195, None
        )
        AddGoal(session.goals)(goal)
        SetAvailability(session.activities, session.availability)(BIG_WEEK)
        SetSessionChoices(session.activities, session.session_choices)(
            SessionChoices.of({Sport.RUNNING: frozenset(SessionType)})
        )

    assert isinstance(run_on_data_thread(qtbot, window, work), JobSucceeded)


def saved_plan(qtbot: QtBot, window: MainWindow) -> Plan | None:
    def work(access: ProfileAccess) -> object:
        return CurrentPlan(access.session.plans)()

    outcome = run_on_data_thread(qtbot, window, work)
    assert isinstance(outcome, JobSucceeded)
    stored = outcome.value
    return None if stored is None else getattr(stored, "state", None)


def test_the_plan_is_rebuilt_after_a_synchronisation_with_the_new_activities(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = linked_window(qtbot, tmp_path, settings, runs=2)
    set_up_goals(qtbot, window)
    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as first:
        assert window.controller.recalculate_overview()
    before = first.args[0]
    assert isinstance(before, PlanBuilt)
    assert before.saved
    _fake_source(window).add_activity(remote_runs(50)[0])  # a recent run arrives from Garmin
    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as second:
        sync_and_wait(qtbot, window)

    after = second.args[0]
    assert isinstance(after, PlanBuilt)
    assert after.saved
    assert after.plan.inputs.fitness.ctl > before.plan.inputs.fitness.ctl
    wait_activities(qtbot, window, 3)
    stored = saved_plan(qtbot, window)
    assert stored is not None
    assert stored.to_json() == after.plan.to_json()
    close_window(qtbot, window)


def test_an_unchanged_overview_does_not_save_the_plan_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = linked_window(qtbot, tmp_path, settings, runs=2)
    set_up_goals(qtbot, window)
    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS):
        assert window.controller.recalculate_overview()

    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as again:
        assert window.controller.recalculate_overview()

    result = again.args[0]
    assert isinstance(result, PlanBuilt)
    assert not result.saved
    close_window(qtbot, window)


def test_without_goals_the_refusal_is_the_state_the_window_is_told(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = linked_window(qtbot, tmp_path, settings, runs=1)

    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as signal:
        assert window.controller.recalculate_overview()

    assert isinstance(signal.args[0], PlanRefused)
    close_window(qtbot, window)


def test_an_import_rebuilds_the_plan_too(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE_NAME)
    set_up_goals(qtbot, window)
    folder = make_import_folder(tmp_path)

    with qtbot.waitSignal(window.controller.plan_rebuilt, timeout=WAIT_MS) as signal:
        window.import_folder(folder)

    result = signal.args[0]
    assert isinstance(result, PlanBuilt)
    assert result.plan.inputs.fitness.ctl > 0.0
    close_window(qtbot, window)


def test_a_plan_that_fails_does_not_fail_the_synchronisation_and_is_logged_once_without_details(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    window = linked_window(qtbot, tmp_path, settings, runs=2)
    set_up_goals(qtbot, window)

    def broken(_self: RebuildPlan, _today: object) -> object:
        raise RuntimeError("pain 7/10 at rest")  # nothing of this text may reach the log

    monkeypatch.setattr(RebuildPlan, "__call__", broken)
    _fake_source(window).add_activity(remote_runs(50)[0])

    with caplog.at_level(logging.INFO):
        sync_and_wait(qtbot, window)
        wait_activities(qtbot, window, 3)
        qtbot.waitUntil(lambda: _plan_failures(caplog) == 1, timeout=WAIT_MS)

    assert isinstance(window.controller.overview_state, Ready)
    assert _plan_failures(caplog) == 1
    assert "pain" not in caplog.text
    assert not [b for b in window.open_boxes if b.objectName() in {"errorMessage"}]
    monkeypatch.undo()
    close_window(qtbot, window)


def _plan_failures(caplog: pytest.LogCaptureFixture) -> int:
    return sum(
        1 for r in caplog.records if r.levelno >= logging.ERROR and "RuntimeError" in r.getMessage()
    )


def _fake_source(window: MainWindow) -> FakeActivitySource:
    source = fakes_of(window).source
    assert isinstance(source, FakeActivitySource)
    return source
