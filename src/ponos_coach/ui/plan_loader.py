# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Goals, availability, session choices and the past of the plan: jobs on the data thread.

Every job builds its use cases inside the data thread with the open profile's connection and hands
the outcome back as a signal on the UI thread; the UI never reads the profile. A change of the
goals, the availability or the session types is a job that saves and then re-reads all three, so the
interface always shows what is really stored. Jobs run in order on the one data thread, so a redo
or an undo lands after the change before it. Health-related data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QObject, Signal

from ponos_coach.goals.domain import GoalId, SessionChoices, WeeklyAvailability
from ponos_coach.goals.ports import Goal
from ponos_coach.goals.use_cases import (
    AddGoal,
    ChangeGoal,
    ListGoals,
    ReadAvailability,
    ReadSessionChoices,
    RemoveGoal,
    SetAvailability,
    SetSessionChoices,
)
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.planning.domain import DayAdherence
from ponos_coach.planning.ports import FrozenDay
from ponos_coach.planning.use_cases import PastPlan
from ponos_coach.profiles.rebuild_plan import PlanAdherence
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


@dataclass(frozen=True, slots=True)
class PlanSnapshot:
    """What the profile holds of the user's plan choices."""

    goals: tuple[Goal, ...]
    availability: WeeklyAvailability | None
    choices: SessionChoices


@dataclass(frozen=True, slots=True)
class PastLoaded:
    """The days from `first` to `last` as they were planned, and how they went."""

    first: date
    last: date
    days: tuple[FrozenDay, ...]
    adherence: tuple[DayAdherence, ...]


def _snapshot(access: ProfileAccess) -> PlanSnapshot:
    session = access.session
    return PlanSnapshot(
        ListGoals(session.goals)(),
        ReadAvailability(session.availability)(),
        ReadSessionChoices(session.session_choices)(),
    )


class PlanLoader(QObject):
    """Runs the jobs of the plan choices; outcomes arrive as signals on the UI thread."""

    snapshot_loaded = Signal(object)  # PlanSnapshot: a plain read
    changed = Signal(object)  # PlanSnapshot: re-read after a change that went through
    command_failed = Signal(object)  # ErrorMessage: a change did not go through, nothing changed
    past_loaded = Signal(object)  # PastLoaded
    failed = Signal(object)  # ErrorMessage: a read failed

    def __init__(self, controller: WorkspaceController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._past_request = 0

    # --- Reading ---------------------------------------------------------------------------

    def load_snapshot(self) -> bool:
        """Read the goals, the availability and the choices. False if no profile is open."""

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, PlanSnapshot):
                self.snapshot_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(_snapshot, done)

    def load_past(self, first: date, last: date, overview: ProfileOverview) -> bool:
        """Read how the days from `first` to `last` were planned and how they went, against the
        loads of `overview`. A newer request supersedes this one. False if no profile is open."""
        self._past_request += 1
        request = self._past_request

        def work(access: ProfileAccess) -> PastLoaded:
            plans = access.session.plans
            days = PastPlan(plans)(first, last)
            adherence = PlanAdherence(plans, lambda: overview, access.today)(first, last)
            return PastLoaded(first, last, days, adherence)

        def done(outcome: JobOutcome) -> None:
            if request != self._past_request:
                return
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, PastLoaded):
                self.past_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def cancel_past(self) -> None:
        """The profile changed: the incoming outcome no longer matters."""
        self._past_request += 1

    # --- Changing --------------------------------------------------------------------------

    def add_goal(self, goal: Goal) -> bool:
        """Add a goal."""
        return self._change(lambda access: AddGoal(access.session.goals)(goal))

    def change_goal(self, goal: Goal) -> bool:
        """Replace the goal with the same id."""
        return self._change(
            lambda access: ChangeGoal(access.session.activities, access.session.goals)(goal)
        )

    def remove_goal(self, goal_id: GoalId) -> bool:
        """Remove a goal."""
        return self._change(
            lambda access: RemoveGoal(access.session.activities, access.session.goals)(goal_id)
        )

    def set_availability(self, availability: WeeklyAvailability | None) -> bool:
        """Set the weekly availability (None forgets it)."""
        return self._change(
            lambda access: SetAvailability(access.session.activities, access.session.availability)(
                availability
            )
        )

    def set_choices(self, choices: SessionChoices) -> bool:
        """Set the session types the plan may use."""
        return self._change(
            lambda access: SetSessionChoices(
                access.session.activities, access.session.session_choices
            )(choices)
        )

    def _change(self, change: Callable[[ProfileAccess], object]) -> bool:
        """Save a change and re-read the three choices, all in one job."""

        def work(access: ProfileAccess) -> PlanSnapshot:
            change(access)
            return _snapshot(access)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, PlanSnapshot):
                self.changed.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.command_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)
