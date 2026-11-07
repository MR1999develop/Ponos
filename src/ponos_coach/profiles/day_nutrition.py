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
from datetime import UTC, date, datetime, time, timedelta
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
    defaults,
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
    AGE_OUT_OF_RANGE = "age_out_of_range"  # the day is more than 130 years after the birth
    UNDER_18 = "under_18"  # younger than 18: the model gives no numbers, only a referral


@dataclass(frozen=True, slots=True)
class NutritionUnavailable:
    """No guidance for the day; `reason` says what is missing (or, for `UNDER_18`, why the model
    gives none: under 18 the app shows no calories, macros or portions and invites the user to
    talk to a doctor or a dietitian)."""

    reason: UnavailableReason


@dataclass(frozen=True, slots=True)
class DayActivity:
    """An activity of the day as the nutrition page lists it.

    `start` is in UTC, `local_start_minute` the minutes after local midnight, `duration_s` the
    moving time, `load` the weighted load (0 if it could not be computed) and `energy` its
    energy (declared by the device, or estimated: `energy.source` says which).
    `local_end_minute` is the wall-clock end in minutes after the local midnight of the start:
    start plus the elapsed time, worked out in UTC and then shown in local time.
    """

    identity: ActivityIdentity
    sport: Sport
    start: datetime
    local_start_minute: int
    duration_s: float
    load: float
    energy: ActivityEnergy
    local_end_minute: float | None = None


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
class _Subject:
    """Who the guidance is for on a day: the person and the heart-rate parameters."""

    person: Person
    physiology: Physiology


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
        subject = self._subject_on(day)
        if isinstance(subject, NutritionUnavailable):
            return subject
        person, physiology = subject.person, subject.physiology
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

    def _subject_on(self, day: date) -> _Subject | NutritionUnavailable:
        """The person as of `day` with the heart-rate parameters, or what is missing. The age
        decides first: nobody under 18 or over 130 gets numbers, whatever else is missing."""
        src = self._src
        fixed = src.fixed_data.get()
        if fixed is None:
            return NutritionUnavailable(UnavailableReason.MISSING_BODY_DATA)
        age = age_on(fixed.birth_date, day)
        age_problem = _age_problem(age)
        if age_problem is not None:
            return NutritionUnavailable(age_problem)
        weight = weight_on(src.weights.list_entries(), day)
        if weight is None:
            return NutritionUnavailable(UnavailableReason.MISSING_WEIGHT)
        try:
            physiology = physiology_for_day(src.physiology.list_entries(), day)
        except MissingPhysiologyError:
            return NutritionUnavailable(UnavailableReason.MISSING_PHYSIOLOGY)
        level = level_for_day(src.activity_levels.list_entries(), day)
        person = Person(physiology.sex, age, fixed.height.cm, float(weight.kg), level)
        return _Subject(person, physiology)

    def _activities_of(
        self, day: date, person: Person, physiology: Physiology
    ) -> tuple[list[_Measured], float | None]:
        """The day's activities with energy and load, and the minutes from the end of the last
        one to the next activity after it (None if there is none or the day is empty)."""
        todays = self._records_of(day)
        if not todays:
            return [], None
        saved_weights = self._src.sport_weights.load()
        measured = [self._measure(r, person, physiology, saved_weights) for r in todays]
        last_end = max(_end_of(r) for r in todays)
        later = self._src.index.first_start_at_or_after(last_end)
        gap = None if later is None else (later - last_end).total_seconds() / _SECONDS_PER_MINUTE
        return measured, gap

    def _records_of(self, day: date) -> list[ActivityRecord]:
        """The records that start on the local `day`: one range query on the start index over a
        window a day wider on each side (a local day is 23 to 25 hours long), then the exact
        local-day filter."""
        zone = self._timezone
        first = datetime.combine(day - timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        last = datetime.combine(day + timedelta(days=2), time.min, tzinfo=zone).astimezone(UTC)
        found = self._src.index.list_started_between(first, last)
        return [r for r in found if local_day_of(r.start, zone) == day]

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
        # a declared figure that is not credible is discarded, and the HR then decides
        credible = declared is not None and declared <= (
            defaults.MAX_ACTIVITY_KCAL_PER_HOUR * record.timer_s / 3600.0
        )
        if cached is None or not credible:
            original = read_original_activity(
                self._src.archive, record, self._decode, self._max_file_bytes
            )
        unreadable = isinstance(original, ExclusionReason)
        activity = None if isinstance(original, ExclusionReason) else original
        if declared is None and activity is not None:
            # indexed before schema v4 (or never declared): the file says what the device wrote
            declared = activity.summary.calories_kcal
        if cached is not None:
            load = cached.load
        else:
            load = (
                0.0
                if activity is None
                else load_of_activity(activity, physiology, saved_weights).load
            )
        average_bpm = _average_bpm(activity, physiology)
        energy = activity_energy(person, record.sport, record.timer_s, declared, average_bpm)
        local = record.start.astimezone(self._timezone)
        start_minute = local.hour * _MINUTES_PER_HOUR + local.minute
        day_activity = DayActivity(
            record.identity,
            record.sport,
            record.start,
            start_minute,
            record.timer_s,
            load,
            energy,
            _local_end_minute(record, local, start_minute, self._timezone),
        )
        return _Measured(day_activity, unreadable)


def _average_bpm(activity: Activity | None, physiology: Physiology) -> float | None:
    """Mean of the heart-rate samples inside the limits, if the activity has a series."""
    if activity is None or activity.heart_rate is None:
        return None
    limits = physiology.limits
    return average_valid_bpm(activity.heart_rate.bpm, limits.min_bpm, limits.max_bpm)


def _age_problem(age: int) -> UnavailableReason | None:
    """Why nobody of this age (in years) gets guidance, if so."""
    if age < 0:
        return UnavailableReason.BEFORE_BIRTH
    if age > defaults.MAX_AGE_YEARS:
        return UnavailableReason.AGE_OUT_OF_RANGE
    if age < defaults.ADULT_AGE_YEARS:
        return UnavailableReason.UNDER_18
    return None


def _end_of(record: ActivityRecord) -> datetime:
    """The one criterion for the end of an activity: its start plus the elapsed time (UTC)."""
    return record.start + timedelta(seconds=record.elapsed_s)


def _local_end_minute(
    record: ActivityRecord, local_start: datetime, start_minute: int, zone: ZoneInfo
) -> float:
    """Wall-clock end in minutes after the local midnight of the start. The end is worked out in
    UTC and only then shown in local time, so a change of daylight saving time during the activity
    moves the clock but not the recovery; on a fall-back it never goes before the start."""
    midnight = datetime.combine(local_start.date(), time.min)
    wall_end = _end_of(record).astimezone(zone).replace(tzinfo=None)
    minutes = (wall_end - midnight).total_seconds() / _SECONDS_PER_MINUTE
    return max(minutes, float(start_minute))


def _session_of(activity: DayActivity) -> TrainingSession:
    return TrainingSession(
        activity.local_start_minute,
        activity.duration_s / _SECONDS_PER_MINUTE,
        activity.energy.net_kcal,
        activity.load,
        activity.local_end_minute,
    )
