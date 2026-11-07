# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case `PlanDayNutrition`: nutrition guidance for one day of the open profile.

It gathers what the nutrition model needs (body data valid that day, activity level, goal, typical
meal times, the day's activities with their energy and load) and hands it to `nutrition.plan_day`;
then it attaches example foods to each meal. It only reads: nothing is saved. General guidance,
never medical advice. Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

from ponos_coach.activities.domain import (
    MAX_ACTIVITY_FILE_BYTES,
    Activity,
    ActivityIdentity,
    ActivityRecord,
    Sport,
)
from ponos_coach.activities.ports import ActivityArchive, ActivityIndex
from ponos_coach.load.domain import (
    MissingPhysiologyError,
    Physiology,
    SportWeight,
    load_fingerprint,
    physiology_for_day,
)
from ponos_coach.load.ports import LoadCache, PhysiologyStore, SportWeightStore
from ponos_coach.load.use_cases import (
    Decoder,
    ExclusionReason,
    load_of_activity,
    local_day_of,
    read_original_activity,
)
from ponos_coach.nutrition.domain import (
    ActivityEnergy,
    DayInput,
    DayNutrition,
    FoodExample,
    MealItem,
    MealTimes,
    NutritionGoal,
    Person,
    TrainingSession,
    activity_energy,
    average_valid_bpm,
    goal_for_day,
    level_for_day,
    plan_day,
    suggest_examples,
)
from ponos_coach.nutrition.food_table import FoodTable
from ponos_coach.nutrition.ports import ActivityLevelStore, GoalPeriodStore, MealTimesStore
from ponos_coach.profiles.domain import age_on, weight_on
from ponos_coach.profiles.ports import FixedDataStore, WeightStore

_SECONDS_PER_MINUTE = 60.0
_MINUTES_PER_HOUR = 60


class UnavailableReason(Enum):
    """Why there is no guidance for a day: data the model cannot do without is missing."""

    MISSING_BODY_DATA = "missing_body_data"  # no height and date of birth in the profile
    MISSING_WEIGHT = "missing_weight"  # no weight entered
    MISSING_PHYSIOLOGY = "missing_physiology"  # no heart-rate parameters (they carry the sex)
    BEFORE_BIRTH = "before_birth"  # the day is before the date of birth


@dataclass(frozen=True, slots=True)
class NutritionUnavailable:
    """No guidance for the day; `reason` says what is missing."""

    reason: UnavailableReason


@dataclass(frozen=True, slots=True)
class DayActivity:
    """An activity of the day as the nutrition page lists it.

    `start` is in UTC, `local_start_minute` the minutes after local midnight, `duration_s` the
    moving time, `load` the weighted load (0 if it could not be computed) and `energy` its
    energy (declared by the device, or estimated: `energy.source` says which).
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_start_minute: int
    duration_s: float
    load: float
    energy: ActivityEnergy


@dataclass(frozen=True, slots=True)
class MealSuggestion:
    """A meal of the day with example foods (possibly none if no combination fits its grams)."""

    meal: MealItem
    examples: tuple[FoodExample, ...]


@dataclass(frozen=True, slots=True)
class DayNutritionReport:
    """Guidance for a day with everything it was computed from.

    `unreadable_activities` counts the day's activities whose original file could not be read:
    they are still included (with the energy and load that do not need the file) but the guidance
    may be less exact. `meal_times` and `goal` are the ones that applied that day.
    """

    day: date
    nutrition: DayNutrition
    goal: NutritionGoal
    meal_times: MealTimes
    meals: tuple[MealSuggestion, ...]
    activities: tuple[DayActivity, ...]
    unreadable_activities: int


type DayNutritionResult = DayNutritionReport | NutritionUnavailable


@dataclass(frozen=True, slots=True)
class NutritionSources:
    """The stores of the open profile that the day's guidance reads."""

    index: ActivityIndex
    archive: ActivityArchive
    load_cache: LoadCache
    physiology: PhysiologyStore
    sport_weights: SportWeightStore
    weights: WeightStore
    fixed_data: FixedDataStore
    activity_levels: ActivityLevelStore
    meal_times: MealTimesStore
    goal_periods: GoalPeriodStore


@dataclass(frozen=True, slots=True)
class _Measured:
    """A day's activity before it becomes a session."""

    activity: DayActivity
    unreadable: bool


class PlanDayNutrition:
    """Nutrition guidance for a day (a query: it saves nothing).

    Deterministic: same profile data, same guidance. Volatile dependencies injected: the stores,
    `decode` (supplied by the Composition Root), the profile's time zone and the food table.
    """

    def __init__(
        self,
        sources: NutritionSources,
        decode: Decoder,
        timezone: ZoneInfo,
        foods: FoodTable,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._src = sources
        self._decode = decode
        self._timezone = timezone
        self._foods = foods
        self._max_file_bytes = max_file_bytes

    def __call__(self, day: date) -> DayNutritionResult:
        """Guidance for `day` (local date in the profile's time zone).

        Raises:
            ActivityIndexError, LoadStoreError, ProfileDataError, NutritionStoreError: a store
                could not be read.
        """
        src = self._src
        fixed = src.fixed_data.get()
        if fixed is None:
            return NutritionUnavailable(UnavailableReason.MISSING_BODY_DATA)
        age = age_on(fixed.birth_date, day)
        if age < 0:
            return NutritionUnavailable(UnavailableReason.BEFORE_BIRTH)
        weight = weight_on(src.weights.list_entries(), day)
        if weight is None:
            return NutritionUnavailable(UnavailableReason.MISSING_WEIGHT)
        try:
            physiology = physiology_for_day(src.physiology.list_entries(), day)
        except MissingPhysiologyError:
            return NutritionUnavailable(UnavailableReason.MISSING_PHYSIOLOGY)
        person = Person(
            physiology.sex,
            age,
            fixed.height.cm,
            float(weight.kg),
            level_for_day(src.activity_levels.list_entries(), day),
        )
        measured, next_gap = self._activities_of(day, person, physiology)
        saved_times = src.meal_times.get()
        meal_times = MealTimes.default() if saved_times is None else saved_times
        goal = goal_for_day(src.goal_periods.list_periods(), day)
        sessions = tuple(_session_of(m.activity) for m in measured)
        nutrition = plan_day(DayInput(person, goal, meal_times, sessions, next_gap))
        suggestions = tuple(
            MealSuggestion(meal, suggest_examples(meal, self._foods.foods))
            for meal in nutrition.meals
        )
        return DayNutritionReport(
            day,
            nutrition,
            goal,
            meal_times,
            suggestions,
            tuple(m.activity for m in measured),
            sum(m.unreadable for m in measured),
        )

    def _activities_of(
        self, day: date, person: Person, physiology: Physiology
    ) -> tuple[list[_Measured], float | None]:
        """The day's activities with energy and load, and the minutes from the end of the last
        one to the next activity after it (None if there is none or the day is empty)."""
        records = self._src.index.list_by_start()
        todays = [r for r in records if local_day_of(r.start, self._timezone) == day]
        if not todays:
            return [], None
        saved_weights = self._src.sport_weights.load()
        measured = [self._measure(r, person, physiology, saved_weights) for r in todays]
        last_end = max(r.start.timestamp() + r.elapsed_s for r in todays)
        later = [r.start.timestamp() for r in records if r.start.timestamp() >= last_end]
        gap = (min(later) - last_end) / _SECONDS_PER_MINUTE if later else None
        return measured, gap

    def _measure(
        self,
        record: ActivityRecord,
        person: Person,
        physiology: Physiology,
        saved_weights: Mapping[Sport, SportWeight],
    ) -> _Measured:
        """Energy and load of one activity; the original file is read only if something that the
        index and the load cache do not hold is needed (the load, or the HR for an estimate)."""
        saved = saved_weights.get(record.sport)
        fingerprint = load_fingerprint(physiology, {} if saved is None else {record.sport: saved})
        cached = self._src.load_cache.get(record.identity, fingerprint)
        declared = record.summary.calories_kcal
        original: Activity | ExclusionReason | None = None
        if cached is None or declared is None:
            original = read_original_activity(
                self._src.archive, record, self._decode, self._max_file_bytes
            )
        unreadable = isinstance(original, ExclusionReason)
        activity = None if isinstance(original, ExclusionReason) else original
        if cached is not None:
            load = cached.load
        else:
            load = (
                0.0
                if activity is None
                else load_of_activity(activity, physiology, saved_weights).load
            )
        average_bpm = None if declared is not None else _average_bpm(activity, physiology)
        energy = activity_energy(person, record.sport, record.timer_s, declared, average_bpm)
        local = record.start.astimezone(self._timezone)
        day_activity = DayActivity(
            record.identity,
            record.sport,
            record.start,
            local.hour * _MINUTES_PER_HOUR + local.minute,
            record.timer_s,
            load,
            energy,
        )
        return _Measured(day_activity, unreadable)


def _average_bpm(activity: Activity | None, physiology: Physiology) -> float | None:
    """Mean of the heart-rate samples inside the limits, if the activity has a series."""
    if activity is None or activity.heart_rate is None:
        return None
    limits = physiology.limits
    return average_valid_bpm(activity.heart_rate.bpm, limits.min_bpm, limits.max_bpm)


def _session_of(activity: DayActivity) -> TrainingSession:
    return TrainingSession(
        activity.local_start_minute,
        activity.duration_s / _SECONDS_PER_MINUTE,
        activity.energy.net_kcal,
        activity.load,
    )
