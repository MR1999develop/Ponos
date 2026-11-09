# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders and gestures shared by the injuries window tests.

The profile and the activities are the made-up ones of `nutrition_support` (today is 30 June 2026,
the athlete "Atleta di prova"). Gestures go through the dialogs, as a person would, and wait for the
data thread. A record that began in the past is written through the use cases on the data thread.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QWizard
from pytestqt.qtbot import QtBot

from ponos_coach.injury.domain import (
    CheckIn,
    IllnessKind,
    Injury,
    InjuryId,
    PainScore,
    Problem,
    Questionnaire,
    WarningSign,
)
from ponos_coach.injury.use_cases import OpenInjury, RecordCheckIn
from ponos_coach.planning.domain import Plan
from ponos_coach.ui.injury_draft import NO_ANSWER
from ponos_coach.ui.injury_presentation import ProblemSummary
from ponos_coach.ui.injury_wizard import InjuryWizard
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.plan_presentation import PlanAvailable
from ponos_coach.ui.workspace import ProfileAccess
from tests.injury.builders import questionnaire
from tests.ui.goals_support import (
    RACE_DAY,
    SECTION_CALENDAR,
    add_race,
    race_ids,
    ready_window,
    set_training,
    wait_for,
    wait_plan,
)
from tests.ui.nutrition_support import cell_of
from tests.ui.workspace_support import TODAY, WAIT_MS


def injury_window(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    """A profile with time to train, a race and its plan, and the injuries read (none yet)."""
    window = ready_window(qtbot, tmp_path, settings)
    set_training(qtbot, window)
    add_race(qtbot, window, RACE_DAY)
    wait_plan(qtbot, window, race_ids(window))
    wait_injuries(qtbot, window)
    return window


def wait_injuries(qtbot: QtBot, window: MainWindow, count: int | None = None) -> None:
    """Wait until the records are read, nothing is on its way, and (if given) there are `count`."""
    wait_for(
        qtbot,
        lambda: (
            window.injuries.is_idle and (count is None or len(window.injuries.summaries) == count)
        ),
    )


def select_record(qtbot: QtBot, window: MainWindow, index: int = 0) -> None:
    """Select in the calendar the day a record began (what the injury commands act on)."""
    day = summary_of(window, index).started_on
    window.sidebar.setCurrentRow(SECTION_CALENDAR)
    window.calendar_page.go_to(day)
    wait_for(qtbot, lambda: bool(cell_of(window, day).problems))


def summary_of(window: MainWindow, index: int = 0) -> ProblemSummary:
    """A record as the window shows it (the first by default)."""
    return window.injuries.summaries[index]


def wait_plan_state(qtbot: QtBot, window: MainWindow, kind: type, *, present: bool = True) -> Plan:
    """Wait until the plan shown has (or lacks) a warning of this kind, and give the plan."""

    def done() -> bool:
        state = window.plan.state
        if not isinstance(state, PlanAvailable):
            return False
        has = any(isinstance(w, kind) for w in state.plan.warnings)
        return has == present

    wait_for(qtbot, lambda: window.plan.is_idle and window.injuries.is_idle and done())
    state = window.plan.state
    assert isinstance(state, PlanAvailable)
    return state.plan


def fill_wizard(
    window: MainWindow,
    *,
    rest: int = 1,
    walking: int = 2,
    running: int = 4,
    morning: int = 2,
    signs: Iterable[WarningSign] = (),
    notes: str = "",
    zone_index: int = 4,  # the shin
) -> InjuryWizard:
    """Answer the questionnaire that is open, up to the last page."""
    wizard = window.injuries.wizard
    assert wizard is not None
    wizard.zone_combo.setCurrentIndex(zone_index)
    wizard.next()
    wizard.rest_spin.setValue(rest)
    wizard.walking_spin.setValue(walking)
    wizard.running_spin.setValue(running)
    wizard.morning_spin.setValue(morning)
    wizard.next()
    wizard.signs_group.set_signs(frozenset(signs))
    wizard.next()
    wizard.notes_edit.setPlainText(notes)
    return wizard


def report_injury(
    qtbot: QtBot,
    window: MainWindow,
    *,
    rest: int = 1,
    walking: int = 2,
    running: int = 4,
    morning: int = 2,
    signs: Iterable[WarningSign] = (),
    notes: str = "",
    zone_index: int = 4,
) -> None:
    """Report an injury through the questionnaire (menu command) and wait until it is stored."""
    wait_injuries(qtbot, window)
    before = len(window.injuries.summaries)
    window.injuries.actions["report_injury"].trigger()
    wait_for(qtbot, lambda: window.injuries.wizard is not None)
    wizard = fill_wizard(
        window,
        rest=rest,
        walking=walking,
        running=running,
        morning=morning,
        signs=signs,
        notes=notes,
        zone_index=zone_index,
    )
    wizard.button(QWizard.WizardButton.FinishButton).click()
    wait_injuries(qtbot, window, before + 1)


def report_illness(qtbot: QtBot, window: MainWindow, kind: IllnessKind) -> None:
    """Report an illness through its dialog and wait until it is stored."""
    wait_injuries(qtbot, window)
    before = len(window.injuries.summaries)
    window.injuries.actions["report_illness"].trigger()
    wait_for(qtbot, lambda: window.injuries.illness_dialog is not None)
    dialog = window.injuries.illness_dialog
    assert dialog is not None
    dialog.radios[kind].setChecked(True)
    dialog.save_button.click()
    wait_injuries(qtbot, window, before + 1)


def check_in(
    qtbot: QtBot,
    window: MainWindow,
    *,
    during: int = NO_ANSWER,
    morning: int = 2,
    signs: Iterable[WarningSign] = (),
) -> None:
    """Answer today's check-in (menu command) and wait until it is stored."""
    wait_injuries(qtbot, window)
    select_record(qtbot, window)
    window.injuries.actions["check_in"].trigger()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is not None)
    dialog = window.injuries.checkin_dialog
    assert dialog is not None
    dialog.during_spin.setValue(during)
    dialog.morning_spin.setValue(morning)
    dialog.signs_group.set_signs(frozenset(signs))
    dialog.save_button.click()
    wait_for(qtbot, lambda: window.injuries.checkin_dialog is None and window.injuries.is_idle)
    wait_for(qtbot, lambda: window.injuries.is_idle)


def undo(window: MainWindow) -> None:
    action = window.injuries.actions["undo"]
    assert action.isEnabled()
    action.trigger()


def redo(window: MainWindow) -> None:
    action = window.injuries.actions["redo"]
    assert action.isEnabled()
    action.trigger()


def checkin(day: date, *, during: int | None, morning: int) -> CheckIn:
    """A check-in with the pain as numbers (`during` is `None` without an activity)."""
    return CheckIn(
        day, None if during is None else PainScore(during), PainScore(morning), frozenset()
    )


def seed_injury(
    qtbot: QtBot,
    window: MainWindow,
    days_ago: int,
    checkins: Iterable[CheckIn] = (),
    q: Questionnaire | None = None,
    injury_id: str = "seed",
) -> None:
    """An injury opened `days_ago` days before today with check-ins, as if the user had been
    following it: written on the data thread by the use cases, then read and the plan rebuilt."""
    injury = Injury(InjuryId(injury_id), TODAY - timedelta(days=days_ago), q or questionnaire(), ())
    entries = tuple(checkins)

    def work(access: ProfileAccess) -> None:
        session = access.session
        OpenInjury(session.injuries)(injury)
        for entry in entries:
            RecordCheckIn(session.activities, session.injuries)(injury.injury_id, entry)

    done: list[bool] = []
    assert window.controller.run_on_profile(work, lambda _outcome: done.append(True))
    wait_for(qtbot, lambda: bool(done))
    window.injuries.loader.load()
    wait_for(
        qtbot,
        lambda: any(s.problem_id == injury_id for s in window.injuries.summaries),
    )
    window.controller.request_overview_refresh()


def stored(window: MainWindow) -> tuple[Problem, ...]:
    """The records as last read, with the domain objects."""
    records = window.injuries.records
    assert records is not None
    return tuple(view.problem for view in records.views)


__all__ = [
    "TODAY",
    "WAIT_MS",
]
