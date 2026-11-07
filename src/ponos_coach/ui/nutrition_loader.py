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

from ponos_coach.nutrition.domain import ActivityLevel, GoalPeriod, MealTimes
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
    DayNutritionResult,
    NutritionSources,
    PlanDayNutrition,
)
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController

FoodTableSource = Callable[[], FoodTable]


@dataclass(frozen=True, slots=True)
class DayLoaded:
    """The guidance (or what is missing) for one day."""

    day: date
    result: DayNutritionResult
    foods_verified: bool  # the food table's values were checked against their source


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
    )


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
    )
    table = foods()
    plan = PlanDayNutrition(sources, access.decode, access.zone, table)
    return DayLoaded(day, plan(day), table.source.verified_on is not None)


class NutritionLoader(QObject):
    """Runs the nutrition jobs; outcomes arrive as signals on the UI thread."""

    day_loaded = Signal(object)  # DayLoaded
    settings_loaded = Signal(object)  # NutritionSettings: read, or re-read after a change
    settings_changed = Signal(object)  # NutritionSettings: only after a change
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

        def work(access: ProfileAccess) -> NutritionSettings:
            return _cases_of(access).read()

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, NutritionSettings):
                self.settings_loaded.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)

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

        def work(access: ProfileAccess) -> NutritionSettings:
            cases = _cases_of(access)
            change(cases)
            return cases.read()

        def done(outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, NutritionSettings):
                self.settings_loaded.emit(outcome.value)
                self.settings_changed.emit(outcome.value)
            elif isinstance(outcome, JobFailed):
                self.command_failed.emit(self._controller.describe_failure(outcome.error))

        return self._controller.run_on_profile(work, done)
