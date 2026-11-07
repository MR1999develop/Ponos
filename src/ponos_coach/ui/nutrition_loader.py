# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nutrition jobs on the data thread: a day's guidance and the nutrition settings.

Every job builds its use case inside the data thread with the open profile's connection and
returns the result as a signal on the UI thread; the UI never reads the profile. A day request
superseded by another (or cancelled) delivers nothing. A change of the settings is a job that saves
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
    is_plausible_lean_mass,
    lean_mass_on,
)
from ponos_coach.nutrition.food_table import FoodTable
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
from ponos_coach.profiles.day_nutrition import (
    DayNutritionReport,
    DayNutritionResult,
    NutritionSources,
    PlanContext,
    PlanDayNutrition,
)
from ponos_coach.profiles.domain import weight_on
from ponos_coach.profiles.lean_mass import (
    ListLeanMass,
    RemoveLeanMass,
    SetLeanMass,
    UndoLeanMass,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.nutrition_undo import LeanMassCell
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController

FoodTableSource = Callable[[], FoodTable]


@dataclass(frozen=True, slots=True)
class DayLoaded:
    """The guidance (or what is missing) for one day."""

    day: date
    result: DayNutritionResult
    foods_verified: bool  # the food table's values were checked against their source
    lean_mass_measured: bool | None  # a measured lean mass was used; None without guidance


@dataclass(frozen=True, slots=True)
class SettingsRead:
    """The nutrition settings and the measured lean mass, read together after every change."""

    settings: NutritionSettings
    lean_masses: tuple[LeanMassEntry, ...]


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
        RemoveLeanMass(session.activities, session.lean_masses),
        ListLeanMass(session.lean_masses),
    )


def _read(cases: _Cases) -> SettingsRead:
    """Everything the settings dialog shows, read in the same job."""
    return SettingsRead(cases.read(), tuple(cases.list_lean_masses()))


def _plan_day(foods: FoodTableSource, day: date, access: ProfileAccess) -> DayLoaded:
    """The data thread job of a day: gather the profile's data and plan the day."""
    session = access.session
    sources = NutritionSources(
        session.activities,
        access.archive,
        session.load_cache,
        session.physiology,
        session.sport_weights,
        session.weights,
        session.fixed_data,
        session.activity_levels,
        session.meal_times,
        session.goal_periods,
        session.lean_masses,
    )
    table = foods()
    context = PlanContext(session.plans, access.today())
    plan = PlanDayNutrition(sources, access.decode, access.zone, table, plan_context=context)
    result = plan(day)
    measured = _lean_mass_measured(access, day) if isinstance(result, DayNutritionReport) else None
    return DayLoaded(day, result, table.source.verified_on is not None, measured)


def _lean_mass_measured(access: ProfileAccess, day: date) -> bool:
    """True if the day's guidance used a measured lean mass: one valid on the day that fits the
    weight of the day (the same rule the model applies; otherwise the estimate is used)."""
    session = access.session
    measured = lean_mass_on(session.lean_masses.list_entries(), day)
    weight = weight_on(session.weights.list_entries(), day)
    return (
        measured is not None
        and weight is not None
        and is_plausible_lean_mass(measured, float(weight.kg))
    )


class NutritionLoader(QObject):
    """Runs the nutrition jobs; outcomes arrive as signals on the UI thread."""

    day_loaded = Signal(object)  # DayLoaded
    settings_loaded = Signal(object)  # NutritionSettings: read, or re-read after a change
    settings_changed = Signal(object)  # NutritionSettings: only after a change
    lean_masses_loaded = Signal(object)  # tuple[LeanMassEntry]: read, or re-read after a change
    lean_mass_applied = Signal(object)  # LeanMassApplied: the first set went through
    lean_mass_refused = Signal(object)  # ErrorMessage: the first set was refused (a field problem)
    command_failed = Signal(object)  # ErrorMessage: a change did not go through, nothing changed
    failed = Signal(object)  # ErrorMessage: a read failed

    def __init__(
        self,
        controller: WorkspaceController,
        foods: FoodTableSource,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._foods = foods
        self._day_request = 0

    # --- A day's guidance ----------------------------------------------------------------

    def load_day(self, day: date) -> bool:
        """Start computing the day's guidance. False if no profile is open or the app closes."""
        self._day_request += 1
        request = self._day_request

        def work(access: ProfileAccess) -> DayLoaded:
            return _plan_day(self._foods, day, access)

        def done(outcome: JobOutcome) -> None:
            if request != self._day_request:
                return
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, DayLoaded):
                self.day_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def cancel_day(self) -> None:
        """The day page was left: the incoming outcome no longer matters."""
        self._day_request += 1

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
        as `lean_mass_refused` (it belongs on the field); success as `lean_mass_applied`."""
        cell = LeanMassCell()

        def work(access: ProfileAccess) -> SettingsRead:
            cases = _cases_of(access)
            cell.value = cases.set_lean_mass(lean_mass, valid_from)
            return _read(cases)

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SettingsRead):
                self._emit_read(outcome.value)
                self.settings_changed.emit(outcome.value.settings)
                self.lean_mass_applied.emit(LeanMassApplied(lean_mass, valid_from, cell))
            elif isinstance(outcome, JobFailed):
                self.lean_mass_refused.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

    def reset_lean_mass(self, lean_mass: LeanMass, valid_from: date, cell: LeanMassCell) -> bool:
        """Set a lean mass again (redo, or undoing a removal); `cell` receives what to undo."""

        def change(cases: _Cases) -> None:
            cell.value = None
            cell.value = cases.set_lean_mass(lean_mass, valid_from)

        return self._change(change)

    def undo_lean_mass(self, cell: LeanMassCell) -> bool:
        """Take a set lean mass back; nothing to do if the set before it failed."""

        def change(cases: _Cases) -> None:
            applied = cell.value
            if applied is not None:
                cases.undo_lean_mass(applied)
                cell.value = None

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
