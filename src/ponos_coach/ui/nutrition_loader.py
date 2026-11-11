# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition jobs on the data thread: a day's guidance and the nutrition settings.

Every job builds its use case inside the data thread with the open profile's connection and
returns the result as a signal on the UI thread; the UI never reads the profile. Every open day has
its own request, so several days load independently; a day request superseded by a newer one for the
same day (or cancelled) delivers nothing. A change of the settings is a job that saves
and then re-reads everything, so the interface always shows what is really stored. Health data:
never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from PySide6.QtCore import QObject, Signal

from ponos_coach.nutrition.domain import (
    ActivityLevel,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    MealTimes,
)
from ponos_coach.nutrition.use_cases import (
    AddGoalPeriod,
    NutritionSettings,
    ReadNutritionSettings,
    RemoveActivityLevel,
    RemoveGoalPeriod,
    ReplaceGoalPeriod,
    SetActivityLevel,
    SetMealTimes,
)
from ponos_coach.planning.domain import PlanDay
from ponos_coach.planning.use_cases import PlannedDay
from ponos_coach.profiles.day_nutrition import (
    DayNutritionResult,
    PlanContext,
    PlanDayNutrition,
)
from ponos_coach.profiles.lean_mass import (
    ListLeanMass,
    RemoveLeanMass,
    RestoreLeanMass,
    SetLeanMass,
    UndoLeanMass,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.nutrition_sources import nutrition_sources
from ponos_coach.ui.nutrition_undo import LeanMassCell
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


@dataclass(frozen=True, slots=True)
class DayLoaded:
    """The guidance (or what is missing) for one day."""

    day: date
    result: DayNutritionResult
    planned: PlanDay | None = None  # what the plan has for the day (shown with its blocks)


@dataclass(frozen=True, slots=True)
class DayFailed:
    """The guidance of one day could not be calculated."""

    day: date
    message: ErrorMessage


@dataclass(frozen=True, slots=True)
class SettingsRead:
    """The nutrition settings and the measured lean mass, read together after every change."""

    settings: NutritionSettings
    lean_masses: tuple[LeanMassEntry, ...]


@dataclass(frozen=True, slots=True)
class _SavedNotRead:
    """A change was saved but the settings could not be read back."""

    error: Exception


@dataclass(frozen=True, slots=True)
class LeanMassApplied:
    """The first set of a lean mass went through: what was set and the cell that holds what to
    undo."""

    lean_mass: LeanMass
    valid_from: date
    cell: LeanMassCell


@dataclass(frozen=True, slots=True)
class _Cases:
    """The settings use cases, built on the open profile inside the data thread."""

    read: ReadNutritionSettings
    set_level: SetActivityLevel
    remove_level: RemoveActivityLevel
    set_meal_times: SetMealTimes
    add_period: AddGoalPeriod
    replace_period: ReplaceGoalPeriod
    remove_period: RemoveGoalPeriod
    set_lean_mass: SetLeanMass
    undo_lean_mass: UndoLeanMass
    restore_lean_mass: RestoreLeanMass
    remove_lean_mass: RemoveLeanMass
    list_lean_masses: ListLeanMass


def _cases_of(access: ProfileAccess) -> _Cases:
    session = access.session
    return _Cases(
        ReadNutritionSettings(session.activity_levels, session.meal_times, session.goal_periods),
        SetActivityLevel(session.activities, session.activity_levels, access.today),
        RemoveActivityLevel(session.activities, session.activity_levels),
        SetMealTimes(session.meal_times),
        AddGoalPeriod(session.activities, session.goal_periods),
        ReplaceGoalPeriod(session.activities, session.goal_periods),
        RemoveGoalPeriod(session.activities, session.goal_periods),
        SetLeanMass(session.activities, session.weights, session.lean_masses, access.today),
        UndoLeanMass(session.activities, session.lean_masses),
        RestoreLeanMass(session.activities, session.lean_masses),
        RemoveLeanMass(session.activities, session.lean_masses),
        ListLeanMass(session.lean_masses),
    )


def _read(cases: _Cases) -> SettingsRead:
    """Everything the settings dialog shows, read in the same job."""
    return SettingsRead(cases.read(), tuple(cases.list_lean_masses()))


def _plan_day(day: date, access: ProfileAccess) -> DayLoaded:
    """The data thread job of a day: gather the profile's data and plan the day."""
    session = access.session
    sources = nutrition_sources(access)
    context = PlanContext(session.plans, access.today())
    plan = PlanDayNutrition(sources, access.decode, access.zone, plan_context=context)
    return DayLoaded(day, plan(day), PlannedDay(session.plans)(day))


class NutritionLoader(QObject):
    """Runs the nutrition jobs; outcomes arrive as signals on the UI thread."""

    day_loaded = Signal(object)  # DayLoaded
    day_failed = Signal(object)  # DayFailed: the guidance of one day could not be calculated
    settings_loaded = Signal(object)  # NutritionSettings: read, or re-read after a change
    settings_changed = Signal(object)  # NutritionSettings: only after a change
    lean_masses_loaded = Signal(object)  # tuple[LeanMassEntry]: read, or re-read after a change
    lean_mass_applied = Signal(object)  # LeanMassApplied: the first set went through
    lean_mass_refused = Signal(object)  # ErrorMessage: the first set was refused (a field problem)
    command_failed = Signal(object)  # ErrorMessage: a change did not go through, nothing changed
    failed = Signal(object)  # ErrorMessage: a read of the settings failed

    def __init__(
        self,
        controller: WorkspaceController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._day_serial = 0
        self._day_requests: dict[date, int] = {}  # the request that counts, for each day

    # --- A day's guidance ----------------------------------------------------------------

    def load_day(self, day: date) -> bool:
        """Start computing the day's guidance. False if no profile is open or the app closes.

        Other days keep loading; a request for the same day made before is dropped."""
        self._day_serial += 1
        request = self._day_serial
        self._day_requests[day] = request

        def work(access: ProfileAccess) -> DayLoaded:
            return _plan_day(day, access)

        def done(outcome: JobOutcome) -> None:
            if self._day_requests.get(day) != request:
                return
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, DayLoaded):
                self.day_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                message = self._controller.describe_failure(outcome.error)
                self.day_failed.emit(DayFailed(day, message))

        return self._controller.run_on_profile(work, done)

    def cancel_day(self, day: date) -> None:
        """The day's window was closed: the incoming outcome no longer matters."""
        self._day_requests.pop(day, None)

    def cancel_days(self) -> None:
        """Every day's window was closed (another profile opened)."""
        self._day_requests.clear()

    # --- Settings -------------------------------------------------------------------------

    def load_settings(self) -> bool:
        """Read the settings. False if no profile is open or the app closes."""

        def work(access: ProfileAccess) -> SettingsRead:
            return _read(_cases_of(access))

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SettingsRead):
                self._emit_read(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def _emit_read(self, read: SettingsRead) -> None:
        self.lean_masses_loaded.emit(read.lean_masses)
        self.settings_loaded.emit(read.settings)

    def set_lean_mass(self, lean_mass: LeanMass, valid_from: date) -> bool:
        """Set the measured lean mass from a day. A rule of the domain that refuses it comes back
        as `lean_mass_refused` (it belongs on the field); success as `lean_mass_applied`.

        If the change is saved but the re-read fails, the change still counts (`lean_mass_applied`,
        so the command enters the undo stack) and the failure comes back as `failed`, followed by
        a new read of the settings."""
        cell = LeanMassCell()

        def work(access: ProfileAccess) -> SettingsRead | _SavedNotRead:
            cases = _cases_of(access)
            cell.value = cases.set_lean_mass(lean_mass, valid_from)
            try:
                return _read(cases)
            except Exception as error:  # the write is done: the read failure is reported apart
                return _SavedNotRead(error)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SettingsRead):
                self._emit_read(outcome.value)
                self.settings_changed.emit(outcome.value.settings)
                self.lean_mass_applied.emit(LeanMassApplied(lean_mass, valid_from, cell))
            elif isinstance(outcome, JobSucceeded) and isinstance(outcome.value, _SavedNotRead):
                self.lean_mass_applied.emit(LeanMassApplied(lean_mass, valid_from, cell))
                self.failed.emit(self._controller.describe_failure(outcome.value.error))
                self.load_settings()
            elif isinstance(outcome, JobFailed):
                self.lean_mass_refused.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def redo_lean_mass(self, cell: LeanMassCell) -> bool:
        """Put back the set that `cell` holds (redo after an undo): the exact entry, if the day
        holds what it held before the set."""

        def change(cases: _Cases) -> None:
            applied = cell.value
            if applied is not None:
                cell.value = cases.restore_lean_mass(applied.entry, applied.replaced)

        return self._change(change)

    def restore_lean_mass(self, entry: LeanMassEntry) -> bool:
        """Put back a removed measurement exactly (undo of a removal)."""

        def change(cases: _Cases) -> None:
            cases.restore_lean_mass(entry)

        return self._change(change)

    def undo_lean_mass(self, cell: LeanMassCell) -> bool:
        """Take a set lean mass back; nothing to do if the set before it failed. The cell keeps
        what was set, so that a redo can put it back."""

        def change(cases: _Cases) -> None:
            applied = cell.value
            if applied is not None:
                cases.undo_lean_mass(applied)

        return self._change(change)

    def remove_lean_mass(self, valid_from: date) -> bool:
        """Remove the measurement of a day."""

        def change(cases: _Cases) -> None:
            cases.remove_lean_mass(valid_from)

        return self._change(change)

    def set_level(self, level: ActivityLevel, valid_from: date) -> bool:
        """Set the activity level from a day."""
        return self._change(lambda cases: cases.set_level(level, valid_from))

    def remove_level(self, valid_from: date) -> bool:
        """Remove the activity level entry of a day."""
        return self._change(lambda cases: cases.remove_level(valid_from))

    def set_meal_times(self, times: MealTimes) -> bool:
        """Set the typical meal times."""
        return self._change(lambda cases: cases.set_meal_times(times))

    def add_period(self, period: GoalPeriod) -> bool:
        """Add a goal period."""
        return self._change(lambda cases: cases.add_period(period))

    def replace_period(self, old_first_day: date, new: GoalPeriod) -> bool:
        """Change the goal period that starts on `old_first_day`."""
        return self._change(lambda cases: cases.replace_period(old_first_day, new))

    def remove_period(self, first_day: date) -> bool:
        """Remove the goal period that starts on `first_day`."""
        return self._change(lambda cases: cases.remove_period(first_day))

    def _change(self, change: Callable[[_Cases], None]) -> bool:
        """Save a change and re-read the settings, all in one job."""

        def work(access: ProfileAccess) -> SettingsRead:
            cases = _cases_of(access)
            change(cases)
            return _read(cases)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SettingsRead):
                self._emit_read(outcome.value)
                self.settings_changed.emit(outcome.value.settings)
            elif isinstance(outcome, JobFailed):
                self.command_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)
