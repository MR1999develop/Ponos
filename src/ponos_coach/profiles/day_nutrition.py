# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case `PlanDayNutrition`: nutrition guidance for one day of the open profile.

For a day after today the activities are the sessions the saved plan has for it, at a typical
time, with the energy of the sport's METs (no heart rate): an estimate from the plan. In the week
of an A or B race and in the taper before it a weight-loss goal gets no deficit (ACSM 2016);
today, which is not in the plan, follows the same rule (race week, or the plan starts in a taper).

It gathers what the nutrition model needs (body data valid that day, activity level, goal, typical
meal times, the day's activities with their energy and load) and hands it to `nutrition.plan_day`.
It only reads: nothing is saved. General guidance,
never medical advice. Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain import (
    MissingPhysiologyError,
    Physiology,
    load_fingerprint,
    physiology_for_day,
)
from ponos_coach.load.ports import LoadCache, PhysiologyStore
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
    GoalPeriod,
    MealTimes,
    NutritionGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
    activity_energy,
    average_valid_bpm,
    defaults,
    goal_for_day,
    is_plausible_lean_mass,
    lean_mass_on,
    level_for_day,
    plan_day,
)
from ponos_coach.nutrition.ports import (
    ActivityLevelStore,
    GoalPeriodStore,
    LeanMassStore,
    MealTimesStore,
)
from ponos_coach.planning.domain import Phase, Plan, PlanDay
from ponos_coach.planning.ports import PlanStore, UnreadablePlan
from ponos_coach.profiles.domain import age_on, weight_on
from ponos_coach.profiles.ports import FixedDataStore, WeightStore

_SECONDS_PER_MINUTE = 60.0
_MINUTES_PER_HOUR = 60
_MINUTES_PER_DAY = 24 * _MINUTES_PER_HOUR
_DAYS_PER_WEEK = 7
# Typical training time of a planned day: the mean start of the sessions of the last 60 days on
# the same weekday, otherwise 18:00 (project defaults).
TYPICAL_TIME_LOOKBACK_DAYS = 60
DEFAULT_TRAINING_MINUTE = 18 * _MINUTES_PER_HOUR
_DEFICIT_PAUSE_PHASES = frozenset({Phase.TAPER, Phase.MINI_TAPER})
_DEFICIT_PAUSE_PRIORITIES = frozenset({Priority.A, Priority.B})


class UnavailableReason(Enum):
    """Why there is no guidance for a day: data the model cannot do without is missing."""

    MISSING_BODY_DATA = "missing_body_data"  # no height and date of birth in the profile
    MISSING_WEIGHT = "missing_weight"  # no weight entered
    MISSING_PHYSIOLOGY = "missing_physiology"  # no heart-rate parameters (they carry the sex)
    BEFORE_BIRTH = "before_birth"  # the day is before the date of birth
    AGE_OUT_OF_RANGE = "age_out_of_range"  # the day is more than 130 years after the birth
    UNDER_18 = "under_18"  # younger than 18: the model gives no numbers, only a referral
    NO_PLAN_FOR_DAY = "no_plan_for_day"  # a day after today that the saved plan does not cover


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
    moving time, `load` the load in TRIMPeq (0 if it could not be computed) and `energy` its
    energy (declared by the device, or estimated: `energy.source` says which).
    `local_end_minute` is the wall-clock end in minutes after the local midnight of the start:
    start plus the elapsed time, worked out in UTC and then shown in local time.
    """

    identity: ActivityIdentity | None  # None for a session of the plan
    sport: Sport
    start: datetime
    local_start_minute: int
    duration_s: float
    load: float
    energy: ActivityEnergy
    local_end_minute: float | None = None


@dataclass(frozen=True, slots=True)
class DayNutritionReport:
    """Guidance for a day with everything it was computed from.

    `unreadable_activities` counts the day's activities whose original file could not be read:
    they are still included (with the energy and load that do not need the file) but the guidance
    may be less exact. `meal_times` and `goal` are the ones that applied that day. `from_plan` marks
    an estimate for a day after today: its activities are the plan's sessions, not recorded ones.
    `lean_mass_measured` says whether the limit on energy availability used a measured lean mass
    (valid on the day and plausible for the weight of the day) instead of the estimate.
    """

    day: date
    nutrition: DayNutrition
    goal: NutritionGoal
    meal_times: MealTimes
    activities: tuple[DayActivity, ...]
    unreadable_activities: int
    from_plan: bool = False
    lean_mass_measured: bool = False


type DayNutritionResult = DayNutritionReport | NutritionUnavailable


@dataclass(frozen=True, slots=True)
class NutritionSources:
    """The stores of the open profile that the day's guidance reads."""

    index: ActivityIndex
    archive: ActivityArchive
    load_cache: LoadCache
    physiology: PhysiologyStore
    weights: WeightStore
    fixed_data: FixedDataStore
    activity_levels: ActivityLevelStore
    meal_times: MealTimesStore
    goal_periods: GoalPeriodStore
    lean_masses: LeanMassStore | None = None  # optional: without it the estimate applies


@dataclass(frozen=True, slots=True)
class PlanContext:
    """What the guidance for a day after `today` needs: the saved plan and today's date (local, in
    the profile's time zone). Without it every day is computed from recorded activities."""

    plans: PlanStore
    today: date


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
    `decode` (supplied by the Composition Root) and the profile's time zone.
    """

    def __init__(
        self,
        sources: NutritionSources,
        decode: Decoder,
        timezone: ZoneInfo,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
        plan_context: PlanContext | None = None,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._src = sources
        self._decode = decode
        self._timezone = timezone
        self._max_file_bytes = max_file_bytes
        self._plan_context = plan_context

    def __call__(self, day: date) -> DayNutritionResult:
        """Guidance for `day` (local date in the profile's time zone).

        A day after today comes from the saved plan (`from_plan`), if a `PlanContext` was given.

        Raises:
            ActivityIndexError, LoadStoreError, ProfileDataError, NutritionStoreError,
            PlanStoreError: a store could not be read.
        """
        src = self._src
        subject = self._subject_on(day)
        if isinstance(subject, NutritionUnavailable):
            return subject
        person, physiology = subject.person, subject.physiology
        context = self._plan_context
        planned_day: PlanDay | None = None
        paused = False
        if context is not None and day > context.today:
            plan = _saved_plan(context.plans)
            planned_day = None if plan is None else _day_of(plan, day)
            if plan is None or planned_day is None:
                return NutritionUnavailable(UnavailableReason.NO_PLAN_FOR_DAY)
            paused = _deficit_paused(plan, day)
            measured, next_gap = self._planned_activities(plan, planned_day, person, context.today)
        else:
            if context is not None and day == context.today:
                # today is not in the plan (it starts tomorrow) but the rule is the same
                plan = _saved_plan(context.plans)
                paused = plan is not None and _deficit_paused(plan, day)
            measured, next_gap = self._activities_of(day, person, physiology)
        saved_times = src.meal_times.get()
        meal_times = MealTimes.default() if saved_times is None else saved_times
        periods = src.goal_periods.list_periods()
        goal = goal_for_day(periods, day)
        sessions = tuple(_session_of(m.activity) for m in measured)
        nutrition = plan_day(
            DayInput(
                person,
                goal,
                meal_times,
                sessions,
                next_gap,
                _loss_days(periods, goal, day),
                paused,
            )
        )
        return DayNutritionReport(
            day,
            nutrition,
            goal,
            meal_times,
            tuple(m.activity for m in measured),
            sum(m.unreadable for m in measured),
            planned_day is not None,
            person.lean_mass_kg is not None,
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
        weight_kg = float(weight.kg)
        lean_mass_kg = self._lean_mass_on(day, weight_kg)
        person = Person(physiology.sex, age, fixed.height.cm, weight_kg, level, lean_mass_kg)
        return _Subject(person, physiology)

    def _lean_mass_on(self, day: date, weight_kg: float) -> float | None:
        """The measured lean mass valid on `day` in kg, if there is one that fits the weight of
        that day (a later weight may have made an old measurement implausible: the estimate then
        applies)."""
        store = self._src.lean_masses
        if store is None:
            return None
        measured = lean_mass_on(store.list_entries(), day)
        if measured is None or not is_plausible_lean_mass(measured, weight_kg):
            return None
        return measured.kg

    def _activities_of(
        self, day: date, person: Person, physiology: Physiology
    ) -> tuple[list[_Measured], float | None]:
        """The day's activities with energy and load, and the minutes from the end of the last
        one to the next activity after it (None if there is none or the day is empty)."""
        todays = self._records_of(day)
        if not todays:
            return [], None
        measured = [self._measure(r, person, physiology) for r in todays]
        last_end = max(_end_of(r) for r in todays)
        later = self._src.index.first_start_at_or_after(last_end)
        gap = None if later is None else (later - last_end).total_seconds() / _SECONDS_PER_MINUTE
        return measured, gap

    def _planned_activities(
        self, plan: Plan, entry: PlanDay, person: Person, today: date
    ) -> tuple[list[_Measured], float | None]:
        """The plan's sessions of the day (a session, or a race) at the typical time of the
        weekday, with the energy of the sport's METs; and the minutes from the end of the last one
        to the first planned session after it (None if there is none or the day is empty)."""
        planned = _planned_sessions(entry)
        if not planned:
            return [], None
        typical = self._typical_minutes(today)
        start_minute = typical.get(entry.day.weekday(), DEFAULT_TRAINING_MINUTE)
        moment = datetime.combine(entry.day, time(), tzinfo=self._timezone) + timedelta(
            minutes=start_minute
        )
        measured = []
        for sport, duration_s, load in planned:
            energy = activity_energy(person, sport, duration_s, None, None)
            end_minute = start_minute + duration_s / _SECONDS_PER_MINUTE
            activity = DayActivity(
                None,
                sport,
                moment.astimezone(UTC),
                start_minute,
                duration_s,
                load,
                energy,
                end_minute,
            )
            measured.append(_Measured(activity, False))
        last_end = max(m.activity.local_end_minute or 0.0 for m in measured)
        return measured, _gap_to_next_session(plan, entry.day, last_end, typical)

    def _typical_minutes(self, today: date) -> dict[int, int]:
        """Mean start (minutes after local midnight, rounded) of the activities of the last 60
        days up to today, by weekday (Monday = 0); a weekday without any is not in the result."""
        zone = self._timezone
        first = datetime.combine(
            today - timedelta(days=TYPICAL_TIME_LOOKBACK_DAYS), time.min, tzinfo=zone
        )
        last = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone)
        found = self._src.index.list_started_between(first.astimezone(UTC), last.astimezone(UTC))
        by_weekday: dict[int, list[int]] = {}
        for record in found:
            local = record.start.astimezone(zone)
            minute = local.hour * _MINUTES_PER_HOUR + local.minute
            by_weekday.setdefault(local.weekday(), []).append(minute)
        return {weekday: round(sum(m) / len(m)) for weekday, m in by_weekday.items()}

    def _records_of(self, day: date) -> list[ActivityRecord]:
        """The records that start on the local `day`: one range query on the start index over a
        window a day wider on each side (a local day is 23 to 25 hours long), then the exact
        local-day filter."""
        zone = self._timezone
        first = datetime.combine(day - timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
        last = datetime.combine(day + timedelta(days=2), time.min, tzinfo=zone).astimezone(UTC)
        found = self._src.index.list_started_between(first, last)
        return [r for r in found if local_day_of(r.start, zone) == day]

    def _measure(self, record: ActivityRecord, person: Person, physiology: Physiology) -> _Measured:
        """Energy and load of one activity; the original file is read only if something that the
        index and the load cache do not hold is needed (the load, or the HR for an estimate)."""
        fingerprint = load_fingerprint(physiology)
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
            load = cached.trimp_eq
        else:
            load = 0.0 if activity is None else load_of_activity(activity, physiology).trimp_eq
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


def _saved_plan(plans: PlanStore) -> Plan | None:
    """The saved plan, or None if there is none, it was refused or it cannot be read."""
    stored = plans.current()
    if stored is None or isinstance(stored, UnreadablePlan) or not isinstance(stored.state, Plan):
        return None
    return stored.state


def _day_of(plan: Plan, day: date) -> PlanDay | None:
    """The plan's entry for `day`, if the plan covers it."""
    for entry in plan.days:
        if entry.day == day:
            return entry
    return None


def _planned_sessions(entry: PlanDay) -> list[tuple[Sport, float, float]]:
    """Sport, duration in seconds and load of what the plan has on a day (session and race)."""
    found = []
    if entry.session is not None:
        session = entry.session
        found.append((session.sport, session.minutes * _SECONDS_PER_MINUTE, session.load))
    if entry.race is not None:
        race = entry.race
        found.append((race.sport, float(race.expected_time_s), race.load))
    return found


def _gap_to_next_session(
    plan: Plan, day: date, last_end_minute: float, typical: Mapping[int, int]
) -> float | None:
    """Minutes from the end of the day's last session to the start of the next planned one."""
    for later in plan.days:
        if later.day <= day or not _planned_sessions(later):
            continue
        start = typical.get(later.day.weekday(), DEFAULT_TRAINING_MINUTE)
        return max(0.0, (later.day - day).days * _MINUTES_PER_DAY + start - last_end_minute)
    return None


def _deficit_paused(plan: Plan, day: date) -> bool:
    """True if `day` is in the taper before an A or B race, or in the Monday-to-Sunday week of one
    (ACSM 2016: lose weight away from races). Today is not in the plan, which starts tomorrow: it
    counts as taper when the first day of the plan is, so that the deficit does not stop only
    tomorrow."""
    entry = _day_of(plan, day)
    if entry is None and plan.days and plan.days[0].day == day + timedelta(days=1):
        entry = plan.days[0]
    if entry is not None and entry.phase in _DEFICIT_PAUSE_PHASES:
        return True
    week_start = day - timedelta(days=day.weekday())
    return any(
        race.priority in _DEFICIT_PAUSE_PRIORITIES
        and 0 <= (race.day - week_start).days < _DAYS_PER_WEEK
        for race in plan.inputs.races
    )


def _loss_days(periods: Sequence[GoalPeriod], goal: NutritionGoal, day: date) -> int | None:
    """Days since the weight-loss period that contains `day` began (0 on its first day), or None
    if the day is not in a weight-loss period."""
    if not isinstance(goal, WeightLossGoal):
        return None
    for period in periods:
        if period.contains(day):
            return (day - period.first_day).days
    return None


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
