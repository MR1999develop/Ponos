# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Acceptance of `PlanDayNutrition` on real FIT files (T-NUT-03, 04, 05).

A day is planned from the profile's data: the activities are written by the fixture generators
(invented people, invented tracks in the open sea), archived, indexed and read back. Checked: days
with 0, 1, 2 and 3 sessions, a session at dawn and at night, an effort of 8 hours, calories that
make no sense, FIT files without calories, other time zones, daylight saving time, and a weight, a
level and a goal that change on the same day.

Defects found were kept as strict xfail until fixed; none is left.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import ActivityOrigin, ActivityRecord, FileFormat
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    Advisory,
    DayLoadLevel,
    EnergySource,
    GoalPeriod,
    MealSlot,
    WeightLossGoal,
)
from ponos_coach.profiles.day_nutrition import (
    DayNutritionReport,
    DayNutritionResult,
    NutritionSources,
    PlanDayNutrition,
)
from ponos_coach.profiles.domain import Weight, WeightEntry
from tests.builders import IMPORTED_AT
from tests.fixtures.generators import fit_writer as fw
from tests.profiles.test_day_nutrition import BORN, DAY, ROME, Rig

STEP_S = 10


class Lab(Rig):
    """The profile of `Rig` (man, 180 cm, 80 kg, active, 30 years old on `DAY`) with builders for
    files of any length and a day planned in any time zone."""

    def __init__(self, *, born: date = BORN) -> None:
        super().__init__(born=born)
        self._serial = 5_000_000

    def add_fit(
        self,
        start: datetime,
        minutes: float,
        *,
        bpm: int | None = 150,
        calories: int | None = None,
        timer_minutes: float | None = None,
        sport: fw.Sport = fw.Sport.RUNNING,
        indexed_before_v4: bool = False,
    ) -> ActivityRecord:
        self._serial += 1
        first = fw.datetime_to_fit(start)
        count = int(minutes * 60) // STEP_S
        samples = [fw.Sample(first + i * STEP_S, bpm) for i in range(count + 1)]
        spec = fw.ActivitySpec(
            start,
            sport,
            fw.SubSport.GENERIC,
            samples,
            serial_number=self._serial,
            total_elapsed_ms=round(minutes * 60_000),
            total_timer_ms=round((timer_minutes or minutes) * 60_000),
            total_calories_kcal=calories,
        )
        return self.add_data(
            fw.build_activity(spec), FileFormat.FIT, indexed_before_v4=indexed_before_v4
        )

    def add_data(
        self, data: bytes, file_format: FileFormat, *, indexed_before_v4: bool = False
    ) -> ActivityRecord:
        activity = decode_stored_activity(data, file_format)
        archived = self.archive.store(data, file_format, activity.start)
        record = ActivityRecord.from_activity(
            activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT
        )
        if indexed_before_v4:  # a v3 index does not know the calories (NULL after the migration)
            record = replace(record, summary=replace(record.summary, calories_kcal=None))
        self.profile.activities.add(record)
        return record

    def plan(
        self,
        day: date = DAY,
        decode: object = None,  # noqa: ARG002 - the signature of `Rig.plan`
    ) -> DayNutritionResult:
        return self.plan_in("Europe/Rome", day)

    def plan_in(self, zone: str, day: date) -> DayNutritionResult:
        p = self.profile
        sources = NutritionSources(
            p.activities, self.archive, p.load_cache, p.physiology,
            p.weights, p.fixed_data, p.activity_levels, p.meal_times, p.goal_periods,
        )  # fmt: skip
        return PlanDayNutrition(sources, decode_stored_activity, ZoneInfo(zone))(day)

    def report_in(self, zone: str, day: date) -> DayNutritionReport:
        report = self.plan_in(zone, day)
        assert isinstance(report, DayNutritionReport)
        return report


def local(hour: int, minute: int = 0, *, day: date = DAY) -> datetime:
    """A time of the day in Rome (UTC+1 in March, before the change) as a UTC instant."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ROME).astimezone(UTC)


def check_report(report: DayNutritionReport) -> None:
    """What must hold of any day (T-NUT-02, T-NUT-04)."""
    n = report.nutrition
    assert min(n.macros.carbs_g, n.macros.protein_g, n.macros.fat_g) >= 0
    assert min(m.macros.carbs_g for m in report.nutrition.meals) >= 0
    assert min(m.macros.protein_g for m in report.nutrition.meals) >= 0
    assert min(m.macros.fat_g for m in report.nutrition.meals) >= 0
    assert sum(m.macros.carbs_g for m in report.nutrition.meals) == n.macros.carbs_g
    assert sum(m.macros.protein_g for m in report.nutrition.meals) == n.macros.protein_g
    assert sum(m.macros.fat_g for m in report.nutrition.meals) == n.macros.fat_g
    assert abs(n.macros.kcal - n.target_kcal) <= 13
    assert n.target_kcal >= n.basal_kcal
    assert n.deficit_kcal <= min(0.2 * n.expenditure_kcal, 750)
    minutes = [m.minute for m in report.nutrition.meals]
    assert minutes == sorted(minutes)


# --- Days with 0, 1, 2 and 3 sessions -------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_t_nut_04_a_day_with_zero_one_two_or_three_files_is_complete_and_consistent(
    count: int,
) -> None:
    lab = Lab()
    starts = [(6, 45), (12, 30), (18, 0)]
    for hour, minute in starts[:count]:
        lab.add_fit(local(hour, minute), 50.0)

    report = lab.report()

    check_report(report)
    assert len(report.activities) == count
    assert (report.nutrition.level is DayLoadLevel.REST) is (count == 0)
    # one recovery per session: a single dose, or the first hour of the hourly refuelling
    recoveries = [
        m
        for m in report.nutrition.meals
        if MealSlot.POST_TRAINING in m.slots and m.refuel_intake in (None, 1)
    ]
    assert len(recoveries) == count


def test_t_nut_04_a_run_at_dawn_before_breakfast_has_a_complete_day() -> None:
    lab = Lab()
    lab.add_fit(local(4, 45), 100.0)  # 04:45-06:25, breakfast would be at 07:30

    report = lab.report()

    check_report(report)
    first = report.nutrition.meals[0]
    assert MealSlot.PRE_TRAINING in first.slots
    assert first.minute == 3 * 60 + 45  # one hour before: two would be before 05:00
    # the recovery (06:55) is 35 minutes from breakfast (07:30, just outside the hour that
    # follows the end): the two become one item at 07:30
    breakfast = next(m for m in report.nutrition.meals if MealSlot.BREAKFAST in m.slots)
    assert not breakfast.moved
    assert breakfast.slots == (MealSlot.BREAKFAST, MealSlot.POST_TRAINING)
    assert breakfast.minute == 7 * 60 + 30


def test_t_nut_04_a_run_at_23_and_one_across_midnight_keep_every_item_inside_the_day() -> None:
    lab = Lab()
    lab.add_fit(local(23, 0), 40.0)
    lab.add_fit(local(23, 45), 90.0)  # ends at 01:15 of the next day

    report = lab.report()

    check_report(report)
    assert all(0 <= m.minute <= 1439 for m in report.nutrition.meals)
    assert len(report.activities) == 2


def test_t_nut_04_an_eight_hour_effort_has_the_during_box_and_stays_inside_the_ceilings() -> None:
    lab = Lab()
    lab.add_fit(local(6, 0), 480.0, bpm=140, sport=fw.Sport.RUNNING)

    report = lab.report()

    check_report(report)
    assert report.nutrition.level is DayLoadLevel.VERY_HIGH
    (box,) = [m for m in report.nutrition.meals if m.hourly is not None]
    assert box.hourly is not None
    assert box.hourly.carbs_g <= 90 and box.hourly.fluid_ml <= 800
    assert report.nutrition.macros.carbs_g <= 12 * 80
    (activity,) = report.activities
    assert activity.energy.source is EnergySource.HEART_RATE
    # Keytel, man of 30 y, 80 kg, 140 bpm: -55.0969 + 88.326 + 15.904 + 6.051 = 55.1841 kJ/min
    # = 13.189 kcal/min; 480 min -> 6330.9 kcal
    assert activity.energy.total_kcal == pytest.approx(6330.9, abs=1.0)


# --- T-NUT-03: calories of the file --------------------------------------------------------


def test_t_nut_03_calories_declared_in_the_file_are_used_and_the_resting_part_removed() -> None:
    lab = Lab()
    lab.add_fit(local(18, 0), 60.0, calories=700)

    (activity,) = lab.report().activities

    assert activity.energy.source is EnergySource.DECLARED
    assert activity.energy.total_kcal == 700.0
    assert activity.energy.net_kcal == pytest.approx(700.0 - 1780.0 / 24.0)  # basal 1780 kcal/day


@pytest.mark.parametrize("raw", [0, 0xFFFF, 50_001, 60_000, 0xFFFE])
def test_t_nut_03_calories_that_cannot_be_right_are_ignored_and_the_activity_is_estimated(
    raw: int,
) -> None:
    lab = Lab()
    lab.add_fit(local(18, 0), 60.0, bpm=150, calories=raw)

    (activity,) = lab.report().activities

    assert activity.energy.source is EnergySource.HEART_RATE
    # Keytel, 30 y, 80 kg, 150 bpm: -55.0969 + 94.635 + 15.904 + 6.051 = 61.4931 kJ/min
    # = 14.697 kcal/min -> 881.8 kcal in an hour
    assert activity.energy.total_kcal == pytest.approx(881.8, abs=0.2)


def test_t_nut_03_the_file_boundary_of_the_calories_is_50000() -> None:
    lab = Lab()
    lab.add_fit(local(18, 0), 24 * 60.0 * 2, calories=50_000)  # two days long: plausible

    (activity,) = lab.report_in("Europe/Rome", date(2026, 3, 2)).activities

    assert activity.energy.source is EnergySource.DECLARED


def test_t_nut_03_a_fit_without_calories_is_estimated_from_its_heart_rate() -> None:
    lab = Lab()
    record = lab.add_fit(local(18, 0), 60.0, bpm=150)

    (activity,) = lab.report().activities

    assert activity.energy.source is EnergySource.HEART_RATE
    assert record.summary.calories_kcal is None
    # 14.697 kcal/min (Keytel, man of 30 y, 80 kg, 150 bpm) over the moving time of the file
    assert activity.energy.total_kcal == pytest.approx(14.697 * record.timer_s / 60.0, rel=1e-3)


def test_t_nut_03_a_fit_without_calories_and_without_heart_rate_uses_the_mets() -> None:
    lab = Lab()
    record = lab.add_fit(local(18, 0), 60.0, bpm=None, sport=fw.Sport.CYCLING)

    (activity,) = lab.report().activities

    assert activity.energy.source is EnergySource.MET
    assert activity.energy.total_kcal == pytest.approx(8.0 * 80.0 * record.timer_s / 3600.0)


def test_t_nut_03_the_day_total_counts_each_activity_net_energy_once() -> None:
    lab = Lab()
    lab.add_fit(local(7, 0), 60.0, calories=600)
    lab.add_fit(local(18, 0), 90.0, calories=900)

    report = lab.report()

    net = (600.0 - 1780.0 / 24.0) + (900.0 - 1780.0 * 1.5 / 24.0)
    assert report.nutrition.expenditure_kcal == round(1780 * 1.4 + net)


# --- T-NUT-03/04: time zone and daylight saving time ---------------------------------------


@pytest.mark.parametrize(
    ("zone", "utc_start", "local_day", "minute"),
    [
        ("Europe/Rome", datetime(2026, 3, 2, 5, 0, tzinfo=UTC), date(2026, 3, 2), 6 * 60),
        ("Pacific/Kiritimati", datetime(2026, 3, 1, 11, 0, tzinfo=UTC), date(2026, 3, 2), 60),
        ("Etc/GMT+12", datetime(2026, 3, 3, 8, 0, tzinfo=UTC), date(2026, 3, 2), 20 * 60),
        ("Asia/Kolkata", datetime(2026, 3, 2, 0, 30, tzinfo=UTC), date(2026, 3, 2), 6 * 60),
        ("UTC", datetime(2026, 3, 2, 0, 0, tzinfo=UTC), date(2026, 3, 2), 0),
    ],
)
def test_t_nut_04_the_profile_time_zone_decides_the_day_and_the_meal_times(
    zone: str, utc_start: datetime, local_day: date, minute: int
) -> None:
    lab = Lab()
    lab.add_fit(utc_start, 50.0)

    report = lab.report_in(zone, local_day)

    (activity,) = report.activities
    assert activity.local_start_minute == minute
    check_report(report)


def test_t_nut_04_an_activity_belongs_to_one_local_day_only() -> None:
    lab = Lab()
    lab.add_fit(datetime(2026, 3, 1, 23, 30, tzinfo=UTC), 30.0)  # 00:30 in Rome on the 2nd

    assert len(lab.report_in("Europe/Rome", date(2026, 3, 1)).activities) == 0
    assert len(lab.report_in("Europe/Rome", date(2026, 3, 2)).activities) == 1
    assert len(lab.report_in("America/Los_Angeles", date(2026, 3, 1)).activities) == 1


def test_t_nut_04_spring_forward_and_fall_back_keep_the_local_minutes_and_the_day() -> None:
    spring = date(2026, 3, 29)  # Rome: 02:00 becomes 03:00
    fall = date(2026, 10, 25)  # Rome: 03:00 becomes 02:00
    lab = Lab()
    lab.add_fit(datetime(2026, 3, 29, 0, 30, tzinfo=UTC), 40.0)  # 01:30 CET
    lab.add_fit(datetime(2026, 3, 29, 1, 30, tzinfo=UTC), 40.0)  # 03:30 CEST (02:30 not there)
    lab.add_fit(datetime(2026, 10, 24, 22, 30, tzinfo=UTC), 40.0)  # 00:30 CEST on the 25th
    lab.add_fit(datetime(2026, 10, 25, 0, 30, tzinfo=UTC), 40.0)  # 02:30 CEST
    lab.add_fit(datetime(2026, 10, 25, 1, 30, tzinfo=UTC), 40.0)  # 02:30 CET, the same wall time
    lab.add_fit(datetime(2026, 10, 25, 22, 30, tzinfo=UTC), 40.0)  # 23:30 CET, the last of the day
    lab.add_fit(datetime(2026, 10, 25, 23, 30, tzinfo=UTC), 40.0)  # 00:30 CET on the 26th

    spring_report = lab.report_in("Europe/Rome", spring)
    fall_report = lab.report_in("Europe/Rome", fall)

    assert [a.local_start_minute for a in spring_report.activities] == [90, 210]
    assert [a.local_start_minute for a in fall_report.activities] == [30, 150, 150, 23 * 60 + 30]
    assert len(lab.report_in("Europe/Rome", date(2026, 10, 26)).activities) == 1
    check_report(spring_report)
    check_report(fall_report)


def test_d_f4b_07_a_session_across_the_spring_change_has_its_recovery_after_it_ends() -> None:
    lab = Lab()
    lab.add_fit(datetime(2026, 3, 29, 0, 10, tzinfo=UTC), 120.0)  # 01:10 CET to 04:10 CEST

    report = lab.report_in("Europe/Rome", date(2026, 3, 29))

    post = next(m for m in report.nutrition.meals if MealSlot.POST_TRAINING in m.slots)
    assert post.minute >= 4 * 60 + 10  # the wall clock shows 04:10 at the end


# --- T-NUT-05: dated changes and determinism ----------------------------------------------


def test_t_nut_05_weight_level_and_goal_changing_on_the_same_day_apply_from_that_day_only() -> None:
    lab = Lab()
    change = date(2026, 3, 10)
    lab.profile.weights.put(WeightEntry(change, Weight(750)))
    lab.profile.activity_levels.put(ActivityLevelEntry(change, ActivityLevel.VERY_ACTIVE))
    lab.profile.goal_periods.add(GoalPeriod(change, change, WeightLossGoal(500)))

    day_before = lab.report(change - timedelta(days=1)).nutrition
    on_day = lab.report(change).nutrition
    day_after = lab.report(change + timedelta(days=1)).nutrition

    # before: 80 kg, active: basal 1780, 1780 x 1.4 = 2492, no goal -> maintenance
    assert (day_before.basal_kcal, day_before.expenditure_kcal) == (1780, 2492)
    assert (day_before.deficit_kcal, day_before.target_kcal) == (0, 2492)
    # that day: 75 kg, very active: basal 1730, x 1.5 = 2595, loss 500 g -> 550 kcal asked, 20%
    # of 2595 = 519 allowed
    assert (on_day.basal_kcal, on_day.expenditure_kcal) == (1730, 2595)
    assert (on_day.deficit_kcal, on_day.target_kcal) == (519, 2076)
    # next day: the goal period is over, weight and level stay
    assert (day_after.basal_kcal, day_after.expenditure_kcal) == (1730, 2595)
    assert (day_after.deficit_kcal, day_after.target_kcal) == (0, 2595)


def test_t_nut_05_the_goal_changing_on_a_day_with_a_session_changes_the_whole_day() -> None:
    lab = Lab()
    lab.add_fit(local(18, 0, day=date(2026, 3, 10)), 120.0, calories=1200)
    lab.profile.goal_periods.add(GoalPeriod(date(2026, 3, 10), None, WeightLossGoal(1000)))

    loss = lab.report(date(2026, 3, 10))
    check_report(loss)

    assert loss.nutrition.deficit_kcal > 0
    assert loss.nutrition.deficit_kcal <= 0.2 * loss.nutrition.expenditure_kcal
    assert Advisory.DEFICIT_LIMITED in loss.nutrition.advisories


def test_t_nut_05_the_same_files_in_another_order_give_the_same_guidance() -> None:
    def build(order: list[int]) -> DayNutritionReport:
        lab = Lab()
        starts = [(6, 45), (12, 30), (18, 0)]
        for i in order:
            lab.add_fit(local(*starts[i]), 50.0, calories=500 + i)
        return lab.report()

    forward, backward = build([0, 1, 2]), build([2, 1, 0])

    assert forward.nutrition == backward.nutrition
    assert [a.local_start_minute for a in forward.activities] == [
        a.local_start_minute for a in backward.activities
    ]


def test_t_nut_05_a_day_planned_twice_is_identical() -> None:
    lab = Lab()
    lab.add_fit(local(7, 0), 90.0)
    lab.add_fit(local(18, 0), 60.0, calories=650)

    assert lab.report() == lab.report()


# --- Defects found -------------------------------------------------------------------------


def test_d_f4b_02_an_energy_the_duration_cannot_explain_is_not_taken_at_face_value() -> None:
    lab = Lab()
    lab.add_fit(local(18, 0), 20.0, calories=50_000)  # 150,000 kcal an hour

    report = lab.report()

    (activity,) = report.activities
    # the highest Compendium value is about 23 METs: 23 x 80 kg x 1/3 h = 613 kcal
    assert activity.energy.total_kcal <= 23.0 * 80.0 * (20.0 / 60.0) * 1.5
    assert report.nutrition.target_kcal < 6000


def test_d_f4b_09_a_migrated_activity_takes_its_declared_calories_from_the_original_file() -> None:
    lab = Lab()
    lab.add_fit(local(18, 0), 60.0, bpm=150, calories=700, indexed_before_v4=True)

    (activity,) = lab.report().activities

    # the original file is read anyway (the load needs it) and it holds the device's 700 kcal
    assert activity.energy.source is EnergySource.DECLARED
    assert activity.energy.total_kcal == 700.0


def test_d_f4b_04_the_recovery_comes_after_the_end_of_a_session_with_long_stops() -> None:
    lab = Lab()
    # 06:00 to 11:00 on the clock, of which 3 hours moving (long stops at the refuges)
    lab.add_fit(local(6, 0), 300.0, timer_minutes=180.0, bpm=130)

    report = lab.report()

    post = next(m for m in report.nutrition.meals if MealSlot.POST_TRAINING in m.slots)
    assert post.minute >= 11 * 60  # eaten at 09:30 today, while the person is still out


def test_d_f4b_05_a_very_old_date_of_birth_does_not_crash_a_far_future_day() -> None:
    lab = Lab(born=date(1900, 1, 1))  # a valid date of birth for the profile

    result = lab.plan_in("Europe/Rome", date(2035, 6, 1))  # 135 years old

    assert result is not None
