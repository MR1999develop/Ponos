# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`PlanDayNutrition`: guidance for a day from the open profile (T-NUT-01, 03, 04, 05)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    Activity,
    ActivityOrigin,
    ActivityRecord,
    ArchivedFile,
    FileFormat,
)
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.load.domain import (
    ActivityLoad,
    HeartRateLimits,
    LoadSource,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
    load_fingerprint,
)
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    Advisory,
    DayLoadLevel,
    EnergySource,
    GoalPeriod,
    MaintenanceGoal,
    MealSlot,
    MealTimes,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.profiles.day_nutrition import (
    DayNutritionReport,
    DayNutritionResult,
    NutritionSources,
    NutritionUnavailable,
    PlanDayNutrition,
    UnavailableReason,
)
from ponos_coach.profiles.domain import (
    BirthDate,
    FixedProfileData,
    Height,
    ProfileId,
    ProfileName,
    Weight,
    WeightEntry,
    age_on,
    weight_on,
)
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import IMPORTED_AT, absolute_path
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fixtures.generators import fit_writer as fw
from tests.food_support import load_food_table

ROME = ZoneInfo("Europe/Rome")
DAY = date(2026, 3, 2)
BORN = date(1996, 3, 1)  # 30 years old on DAY
PHYSIOLOGY = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
FOODS = load_food_table()


class CountingArchive(InMemoryActivityArchive):
    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        self.reads += 1
        return super().read(file, max_bytes)


def decode_fit(data: bytes, file_format: FileFormat) -> Activity:
    assert file_format is FileFormat.FIT
    return decode_activity(data, FitLimits())


def fit_bytes(
    start: datetime,
    serial: int,
    *,
    bpm: int | None = 150,
    minutes: int = 60,
    sport: fw.Sport = fw.Sport.RUNNING,
    calories: int | None = None,
) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, bpm) for i in range(minutes * 60)]
    spec = fw.ActivitySpec(
        start,
        sport,
        fw.SubSport.GENERIC,
        samples,
        serial_number=serial,
        total_calories_kcal=calories,
    )
    return fw.build_activity(spec)


class Rig:
    """A profile with body data (male, 180 cm, born 1996-03-01, 80 kg from 1 March)."""

    def __init__(
        self, *, body: bool = True, weight: bool = True, hr: bool = True, born: date = BORN
    ) -> None:
        self.archive = CountingArchive()
        self.profile: OpenProfile = InMemoryProfileStorage().create(
            absolute_path("p"),
            ProfileId.parse("00000000-0000-4000-8000-000000000001"),
            ProfileName.parse("Atleta di prova"),
            "Europe/Rome",
        )
        if body:
            self.profile.fixed_data.set_once(FixedProfileData(Height(180), BirthDate(born)))
        if weight:
            self.profile.weights.put(WeightEntry(date(2026, 3, 1), Weight(800)))
        if hr:
            self.profile.physiology.put(PhysiologyEntry(date(2026, 1, 1), PHYSIOLOGY))
        self.profile.activity_levels.put(ActivityLevelEntry(date(2026, 1, 1), ActivityLevel.ACTIVE))

    def add_file(self, data: bytes) -> ActivityRecord:
        activity = decode_fit(data, FileFormat.FIT)
        archived = self.archive.store(data, FileFormat.FIT, activity.start)
        record = ActivityRecord.from_activity(
            activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT
        )
        self.profile.activities.add(record)
        return record

    def cache_load(self, record: ActivityRecord, load: float) -> None:
        fingerprint = load_fingerprint(PHYSIOLOGY, {})
        value = ActivityLoad(load, load, load, 3000, 0, 0.0, LoadSource.HEART_RATE)
        with self.profile.activities.transaction():
            self.profile.load_cache.put(record.identity, fingerprint, value)

    def plan(self, day: date = DAY, decode: object = decode_fit) -> DayNutritionResult:
        p = self.profile
        sources = NutritionSources(
            p.activities, self.archive, p.load_cache, p.physiology, p.sport_weights,
            p.weights, p.fixed_data, p.activity_levels, p.meal_times, p.goal_periods,
        )  # fmt: skip
        return PlanDayNutrition(sources, decode, ROME, FOODS)(day)  # type: ignore[arg-type]

    def report(self, day: date = DAY) -> DayNutritionReport:
        result = self.plan(day)
        assert isinstance(result, DayNutritionReport)
        return result


def at(hour: int, minute: int = 0, day: int = 2) -> datetime:
    """A local (Rome, UTC+1 in March) time as a UTC instant."""
    return datetime(2026, 3, day, hour - 1, minute, tzinfo=UTC)


# --- Body data valid that day -------------------------------------------------------------


@pytest.mark.parametrize(
    ("rig", "reason"),
    [
        (Rig(body=False), UnavailableReason.MISSING_BODY_DATA),
        (Rig(weight=False), UnavailableReason.MISSING_WEIGHT),
        (Rig(hr=False), UnavailableReason.MISSING_PHYSIOLOGY),
    ],
)
def test_without_the_data_the_model_needs_there_is_no_guidance_and_the_reason_is_given(
    rig: Rig, reason: UnavailableReason
) -> None:
    assert rig.plan() == NutritionUnavailable(reason)


def test_a_day_before_the_birth_has_no_guidance() -> None:
    assert Rig().plan(date(1996, 2, 29)) == NutritionUnavailable(UnavailableReason.BEFORE_BIRTH)


def test_age_is_counted_in_complete_years_on_the_day() -> None:
    assert age_on(BirthDate(BORN), date(2026, 2, 28)) == 29
    assert age_on(BirthDate(BORN), date(2026, 3, 1)) == 30  # on the birthday itself
    assert age_on(BirthDate(date(2000, 2, 29)), date(2026, 2, 28)) == 25
    assert age_on(BirthDate(date(2000, 2, 29)), date(2026, 3, 1)) == 26


def test_the_weight_is_the_latest_entry_and_the_first_one_covers_earlier_days() -> None:
    entries = [
        WeightEntry(date(2026, 3, 1), Weight(800)),
        WeightEntry(date(2026, 3, 10), Weight(750)),
    ]

    assert weight_on(entries, date(2026, 1, 1)) == Weight(800)  # before the first entry
    assert weight_on(entries, date(2026, 3, 9)) == Weight(800)
    assert weight_on(entries, date(2026, 3, 10)) == Weight(750)
    assert weight_on([], date(2026, 3, 10)) is None


# --- A day without activities -------------------------------------------------------------


def test_a_rest_day_gets_the_three_meals_with_example_foods() -> None:
    report = Rig().report()

    assert report.activities == ()
    assert report.nutrition.level is DayLoadLevel.REST
    assert (report.nutrition.basal_kcal, report.nutrition.expenditure_kcal) == (1780, 2492)
    assert [m.meal.slots for m in report.meals] == [
        (MealSlot.BREAKFAST,),
        (MealSlot.LUNCH,),
        (MealSlot.DINNER,),
    ]
    assert all(m.examples for m in report.meals)
    assert report.goal == MaintenanceGoal()
    assert report.unreadable_activities == 0


# --- T-NUT-03: energy of the day's activities ---------------------------------------------


def test_the_energy_declared_by_the_device_is_used_and_no_file_is_read_with_a_cached_load() -> None:
    rig = Rig()
    record = rig.add_file(fit_bytes(at(18), 1, calories=700))
    rig.cache_load(record, 90.0)

    report = rig.report()

    (activity,) = report.activities
    assert activity.energy.source is EnergySource.DECLARED
    assert activity.energy.total_kcal == 700.0
    assert activity.energy.net_kcal == pytest.approx(700.0 - 1780.0 / 24.0)
    assert activity.load == 90.0
    assert rig.archive.reads == 0
    # the resting part is not counted twice: expenditure = basal x level + net, not + total
    assert report.nutrition.expenditure_kcal == round(1780 * 1.4 + 700.0 - 1780.0 / 24.0)


def test_without_declared_energy_the_average_heart_rate_gives_a_keytel_estimate() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=140))

    (activity,) = rig.report().activities

    assert activity.energy.source is EnergySource.HEART_RATE
    assert activity.energy.total_kcal == pytest.approx(791.36, abs=0.01)  # 30 y, 80 kg, 140 bpm
    assert activity.energy.net_kcal == pytest.approx(791.36 - 1780.0 / 24.0, abs=0.01)


def test_without_heart_rate_the_sport_mets_are_used() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=None, sport=fw.Sport.WALKING))

    (activity,) = rig.report().activities

    assert activity.energy.source is EnergySource.MET
    assert activity.energy.total_kcal == pytest.approx(3.5 * 80.0)  # 3.5 METs x 80 kg x 1 h


def test_heart_rate_outside_the_limits_is_not_averaged() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=200))  # above the 190 limit: every sample ignored

    (activity,) = rig.report().activities

    assert activity.energy.source is EnergySource.MET


def test_an_unreadable_file_is_counted_and_the_activity_still_counts_with_the_mets() -> None:
    rig = Rig()
    record = rig.add_file(fit_bytes(at(18), 1, bpm=150))
    del rig.archive.files[record.archived.relative_path]

    report = rig.report()

    assert report.unreadable_activities == 1
    (activity,) = report.activities
    assert activity.energy.source is EnergySource.MET
    assert activity.load == 0.0


def test_a_file_that_cannot_be_decoded_is_counted_too() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1))

    def broken(data: bytes, file_format: FileFormat) -> Activity:
        raise ActivityFileError(ActivityFileReason.CORRUPT)

    result = rig.plan(decode=broken)

    assert isinstance(result, DayNutritionReport)
    assert result.unreadable_activities == 1


def test_the_load_is_computed_from_the_file_when_it_is_not_cached() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, bpm=150, calories=700))

    (activity,) = rig.report().activities

    assert activity.load > 0.0
    assert rig.archive.reads == 1


# --- T-NUT-04: activities of the day shape the meals --------------------------------------


def test_the_local_start_time_of_the_activity_decides_the_meals() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(12, 30), 1, calories=600))  # 12:30 local, an hour at lunch time

    report = rig.report()

    (activity,) = report.activities
    assert activity.local_start_minute == 12 * 60 + 30
    lunch = next(m.meal for m in report.meals if MealSlot.LUNCH in m.meal.slots)
    assert lunch.moved
    assert MealSlot.POST_TRAINING in lunch.slots


def test_two_activities_in_one_day_are_both_listed_and_both_shape_the_day() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(7), 1, calories=500))
    rig.add_file(fit_bytes(at(18), 2, calories=500))

    report = rig.report()

    assert [a.local_start_minute for a in report.activities] == [7 * 60, 18 * 60]
    slots = [slot for m in report.meals for slot in m.meal.slots]
    assert slots.count(MealSlot.POST_TRAINING) == 2


def test_only_the_activities_of_the_local_day_count() -> None:
    rig = Rig()
    rig.add_file(
        fit_bytes(datetime(2026, 3, 2, 23, 30, tzinfo=UTC), 1, calories=500)
    )  # 00:30 on the 3rd
    rig.add_file(fit_bytes(at(18, day=2), 2, calories=500))

    assert len(rig.report(date(2026, 3, 2)).activities) == 1
    assert len(rig.report(date(2026, 3, 3)).activities) == 1


def evening_dinner_carbs(next_start: datetime | None) -> int:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, calories=500))
    if next_start is not None:
        rig.add_file(fit_bytes(next_start, 2, calories=500))
    return next(
        m.meal for m in rig.report().meals if MealSlot.DINNER in m.meal.slots
    ).macros.carbs_g


def test_a_session_within_8_hours_adds_recovery_carbohydrates_to_the_evening_one() -> None:
    # the evening session ends at 19:00 and the next starts at 02:00: 7 h later
    assert evening_dinner_carbs(at(2, day=3)) > evening_dinner_carbs(None)


def test_a_session_the_next_morning_is_too_far_for_rapid_refuelling() -> None:
    # 19:00 to 07:00 is 12 h: more than the 8 h of rapid refuelling
    assert evening_dinner_carbs(at(7, day=3)) == evening_dinner_carbs(None)


# --- T-NUT-05: determinism and dated changes ----------------------------------------------


def test_the_same_data_always_gives_the_same_guidance() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(7), 1, bpm=145))

    assert rig.plan() == rig.plan()


def test_a_change_of_weight_applies_only_from_its_own_day() -> None:
    rig = Rig()
    rig.profile.weights.put(WeightEntry(date(2026, 3, 10), Weight(750)))

    before = rig.report(date(2026, 3, 9)).nutrition
    on_day = rig.report(date(2026, 3, 10)).nutrition

    assert before.basal_kcal == 1780
    assert on_day.basal_kcal == 1730  # 5 kg less: 50 kcal less
    assert rig.report(date(2026, 3, 1)).nutrition.basal_kcal == 1780


def test_a_goal_period_applies_from_its_first_day_to_its_last() -> None:
    rig = Rig()
    rig.profile.goal_periods.add(
        GoalPeriod(date(2026, 3, 10), date(2026, 3, 20), WeightLossGoal(500))
    )
    rig.profile.goal_periods.add(GoalPeriod(date(2026, 3, 21), None, PerformanceGoal()))

    assert rig.report(date(2026, 3, 9)).nutrition.deficit_kcal == 0
    # 500 g a week asks 550 kcal; on a rest day 20% of the expenditure (2492) is 498
    assert rig.report(date(2026, 3, 10)).nutrition.deficit_kcal == 498
    assert rig.report(date(2026, 3, 20)).nutrition.deficit_kcal == 498
    assert rig.report(date(2026, 3, 21)).nutrition.deficit_kcal == 0
    assert rig.report(date(2026, 3, 21)).goal == PerformanceGoal()


def test_the_activity_level_in_force_that_day_changes_the_expenditure() -> None:
    rig = Rig()
    rig.profile.activity_levels.put(
        ActivityLevelEntry(date(2026, 3, 10), ActivityLevel.VERY_ACTIVE)
    )

    assert rig.report(date(2026, 3, 9)).nutrition.expenditure_kcal == 2492  # 1780 x 1.4
    assert rig.report(date(2026, 3, 10)).nutrition.expenditure_kcal == 2670  # 1780 x 1.5


def test_before_the_first_level_entry_sedentary_is_assumed_and_the_user_is_told() -> None:
    rig = Rig()

    assert Advisory.ACTIVITY_LEVEL_ASSUMED not in rig.report().nutrition.advisories
    early = rig.report(date(2025, 12, 31))

    assert Advisory.ACTIVITY_LEVEL_ASSUMED in early.nutrition.advisories
    assert early.nutrition.expenditure_kcal == 2142  # basal 1785 (29 years old) x 1.2


def test_the_saved_meal_times_are_the_ones_used() -> None:
    rig = Rig()
    times = MealTimes(time(6, 30), time(12, 0), time(19, 0))
    rig.profile.meal_times.set(times)

    report = rig.report()

    assert report.meal_times == times
    assert [m.meal.minute for m in report.meals] == [6 * 60 + 30, 12 * 60, 19 * 60]


def test_a_minor_gets_no_numbers_at_all_only_the_under_18_outcome() -> None:
    rig = Rig(born=date(2008, 6, 1))  # 17 years old on the day
    rig.profile.goal_periods.add(GoalPeriod(date(2026, 3, 1), None, WeightLossGoal(500)))

    assert rig.plan() == NutritionUnavailable(UnavailableReason.UNDER_18)


def test_the_day_before_the_eighteenth_birthday_is_still_under_18_and_the_birthday_is_not() -> None:
    rig = Rig(born=date(2008, 3, 3))

    assert rig.plan(date(2026, 3, 2)) == NutritionUnavailable(UnavailableReason.UNDER_18)
    assert isinstance(rig.plan(date(2026, 3, 3)), DayNutritionReport)


def test_a_minor_is_told_so_even_when_the_weight_is_missing() -> None:
    rig = Rig(born=date(2008, 6, 1), weight=False)

    assert rig.plan() == NutritionUnavailable(UnavailableReason.UNDER_18)


@pytest.mark.parametrize(("day", "reason"), [
    (date(2127, 3, 1), UnavailableReason.AGE_OUT_OF_RANGE),  # 131 years
    (date(9999, 12, 31), UnavailableReason.AGE_OUT_OF_RANGE),
])  # fmt: skip
def test_a_day_beyond_130_years_from_the_birth_is_unavailable_not_an_error(
    day: date, reason: UnavailableReason
) -> None:
    assert Rig().plan(day) == NutritionUnavailable(reason)


def test_the_age_of_130_is_still_planned() -> None:
    assert isinstance(Rig(born=date(1900, 1, 1)).plan(date(2030, 1, 1)), DayNutritionReport)


def test_an_adult_with_the_same_goal_gets_the_deficit() -> None:
    rig = Rig()
    rig.profile.goal_periods.add(GoalPeriod(date(2026, 3, 1), None, WeightLossGoal(500)))

    assert rig.report().nutrition.deficit_kcal == 498  # the 20% cap of 2492 kcal


# --- The day is read with range queries, not by listing the whole index ---------------------------


def test_a_day_is_read_with_range_queries_and_never_by_listing_the_whole_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1))
    rig.add_file(fit_bytes(at(10, day=5), 2))  # three days later
    index_type = type(rig.profile.activities)
    monkeypatch.setattr(index_type, "list_by_start", lambda _self: pytest.fail("whole index"))

    report = rig.report()

    assert len(report.activities) == 1


def test_activities_either_side_of_the_local_midnight_belong_to_their_own_day() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(23, 30), 1, minutes=10))
    rig.add_file(fit_bytes(datetime(2026, 3, 2, 23, 30, tzinfo=UTC), 2, minutes=10))  # 00:30 local

    assert len(rig.report(DAY).activities) == 1
    assert len(rig.report(date(2026, 3, 3)).activities) == 1


def test_the_next_activity_is_found_even_days_after_the_day() -> None:
    rig = Rig()
    rig.add_file(fit_bytes(at(18), 1, minutes=60))
    rig.add_file(fit_bytes(at(7, day=9), 2, minutes=60))

    assert len(rig.report().activities) == 1
