# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of the nutrition stores and of the activity calories kept by the index.

They are tried through a profile created by the `ProfileStorage` of the `harness` fixture, on the
in-memory fakes and on SQLite alike.
"""

from __future__ import annotations

from contextlib import closing
from datetime import date, time

import pytest

from ponos_coach.activities.domain import ActivitySummary
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    ActivityLevelEntry,
    GoalPeriod,
    LeanMass,
    LeanMassEntry,
    MaintenanceGoal,
    MealTimes,
    PerformanceGoal,
    WeightLossGoal,
)
from ponos_coach.nutrition.ports import (
    ActivityLevelNotFoundError,
    GoalPeriodNotFoundError,
    GoalPeriodOverlapError,
    LeanMassNotFoundError,
)
from tests.builders import fit_identity, make_record
from tests.contracts.contract_storage import NAME, PROFILE_ID, create
from tests.contracts.harness import StorageHarness

D1 = date(2026, 1, 1)
D2 = date(2026, 2, 1)
D3 = date(2026, 3, 1)


class ActivityCaloriesContract:
    """The calories declared by the device travel with the activity."""

    def test_calories_are_kept_with_the_record(self, harness: StorageHarness) -> None:
        record = make_record(summary=ActivitySummary(distance_m=5000.0, calories_kcal=612.0))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            assert profile.activities.get(record.identity) == record
            assert profile.activities.list_by_start()[0].summary.calories_kcal == 612.0

    def test_a_record_without_calories_comes_back_without_them(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(fit_identity(2))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            stored = profile.activities.get(record.identity)

        assert stored is not None
        assert stored.summary.calories_kcal is None


class ActivityLevelStoreContract:
    """Activity level outside sport, with validity date."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.activity_levels.list_entries() == []

    def test_entries_are_listed_by_date(self, harness: StorageHarness) -> None:
        later = ActivityLevelEntry(D3, ActivityLevel.VERY_ACTIVE)
        earlier = ActivityLevelEntry(D1, ActivityLevel.LIGHT)
        with closing(create(harness)) as profile:
            profile.activity_levels.put(later)
            profile.activity_levels.put(earlier)

            assert list(profile.activity_levels.list_entries()) == [earlier, later]

    def test_same_date_replaces_the_entry(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activity_levels.put(ActivityLevelEntry(D1, ActivityLevel.LIGHT))
            profile.activity_levels.put(ActivityLevelEntry(D1, ActivityLevel.ACTIVE))

            assert list(profile.activity_levels.list_entries()) == [
                ActivityLevelEntry(D1, ActivityLevel.ACTIVE)
            ]

    def test_remove_deletes_only_the_entry_of_that_date(self, harness: StorageHarness) -> None:
        first = ActivityLevelEntry(D1, ActivityLevel.LIGHT)
        with closing(create(harness)) as profile:
            profile.activity_levels.put(first)
            profile.activity_levels.put(ActivityLevelEntry(D2, ActivityLevel.ACTIVE))

            profile.activity_levels.remove(D2)

            assert list(profile.activity_levels.list_entries()) == [first]

    def test_removing_a_missing_entry_is_an_error_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        entry = ActivityLevelEntry(D1, ActivityLevel.LIGHT)
        with closing(create(harness)) as profile:
            profile.activity_levels.put(entry)

            with pytest.raises(ActivityLevelNotFoundError):
                profile.activity_levels.remove(D2)

            assert list(profile.activity_levels.list_entries()) == [entry]

    @pytest.mark.parametrize("level", list(ActivityLevel))
    def test_every_level_survives_closing_and_reopening(
        self, harness: StorageHarness, level: ActivityLevel
    ) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.activity_levels.put(ActivityLevelEntry(D1, level))
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert list(reopened.activity_levels.list_entries()) == [ActivityLevelEntry(D1, level)]

    def test_a_cancelled_transaction_leaves_no_level(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.activity_levels.put(ActivityLevelEntry(D1, ActivityLevel.LIGHT))
                raise RuntimeError("boom")

            assert profile.activity_levels.list_entries() == []


class LeanMassStoreContract:
    """Measured lean mass: a dated series, one entry per date."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.lean_masses.list_entries() == []

    def test_entries_are_listed_by_date_and_kept_whole(self, harness: StorageHarness) -> None:
        later = LeanMassEntry(D2, LeanMass(612))
        earlier = LeanMassEntry(D1, LeanMass(605))
        with closing(create(harness)) as profile:
            profile.lean_masses.put(later)
            profile.lean_masses.put(earlier)

            assert list(profile.lean_masses.list_entries()) == [earlier, later]

    def test_same_date_replaces_the_entry(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.lean_masses.put(LeanMassEntry(D1, LeanMass(600)))
            profile.lean_masses.put(LeanMassEntry(D1, LeanMass(610)))

            assert list(profile.lean_masses.list_entries()) == [LeanMassEntry(D1, LeanMass(610))]

    def test_the_limits_of_the_type_are_kept_exactly(self, harness: StorageHarness) -> None:
        entries = [LeanMassEntry(D1, LeanMass(150)), LeanMassEntry(D2, LeanMass(2000))]
        with closing(create(harness)) as profile:
            for entry in entries:
                profile.lean_masses.put(entry)

            assert list(profile.lean_masses.list_entries()) == entries

    def test_remove_deletes_only_the_entry_of_that_date(self, harness: StorageHarness) -> None:
        first = LeanMassEntry(D1, LeanMass(600))
        with closing(create(harness)) as profile:
            profile.lean_masses.put(first)
            profile.lean_masses.put(LeanMassEntry(D2, LeanMass(610)))

            profile.lean_masses.remove(D2)

            assert list(profile.lean_masses.list_entries()) == [first]

    def test_removing_a_missing_entry_is_an_error_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        entry = LeanMassEntry(D1, LeanMass(600))
        with closing(create(harness)) as profile:
            profile.lean_masses.put(entry)

            with pytest.raises(LeanMassNotFoundError):
                profile.lean_masses.remove(D2)

            assert list(profile.lean_masses.list_entries()) == [entry]

    def test_entries_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        entry = LeanMassEntry(D1, LeanMass(655))
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.lean_masses.put(entry)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert list(reopened.lean_masses.list_entries()) == [entry]

    def test_a_cancelled_transaction_leaves_no_entry(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.lean_masses.put(LeanMassEntry(D1, LeanMass(600)))
                raise RuntimeError("boom")

            assert profile.lean_masses.list_entries() == []


class MealTimesStoreContract:
    """The typical meal times: one set, absent until set."""

    TIMES = MealTimes(time(6, 45), time(12, 15), time(19, 30))

    def test_a_new_profile_has_no_saved_times(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.meal_times.get() is None

    def test_set_then_get(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.meal_times.set(self.TIMES)

            assert profile.meal_times.get() == self.TIMES

    def test_setting_again_replaces_the_times(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.meal_times.set(self.TIMES)
            profile.meal_times.set(MealTimes.default())

            assert profile.meal_times.get() == MealTimes.default()

    def test_the_extreme_times_of_the_day_are_kept(self, harness: StorageHarness) -> None:
        extreme = MealTimes(time(0, 0), time(12, 0), time(23, 59))
        with closing(create(harness)) as profile:
            profile.meal_times.set(extreme)

            assert profile.meal_times.get() == extreme

    def test_times_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.meal_times.set(self.TIMES)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.meal_times.get() == self.TIMES


class GoalPeriodStoreContract:
    """Goal periods: listed by first day, never overlapping."""

    LOSS = GoalPeriod(D1, date(2026, 1, 31), WeightLossGoal(500))
    PERFORMANCE = GoalPeriod(D2, date(2026, 2, 28), PerformanceGoal())
    OPEN = GoalPeriod(D3, None, MaintenanceGoal())

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.goal_periods.list_periods() == []

    def test_periods_are_listed_by_first_day_with_their_goals(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            for period in (self.OPEN, self.LOSS, self.PERFORMANCE):
                profile.goal_periods.add(period)

            assert list(profile.goal_periods.list_periods()) == [
                self.LOSS,
                self.PERFORMANCE,
                self.OPEN,
            ]

    def test_overlapping_periods_are_refused_and_change_nothing(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goal_periods.add(self.LOSS)

            with pytest.raises(GoalPeriodOverlapError):
                profile.goal_periods.add(GoalPeriod(date(2026, 1, 31), None, PerformanceGoal()))
            with pytest.raises(GoalPeriodOverlapError):
                profile.goal_periods.add(GoalPeriod(D1, None, PerformanceGoal()))

            assert list(profile.goal_periods.list_periods()) == [self.LOSS]

    def test_periods_that_touch_without_sharing_a_day_are_accepted(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goal_periods.add(self.LOSS)
            profile.goal_periods.add(self.PERFORMANCE)

            assert len(profile.goal_periods.list_periods()) == 2

    def test_an_open_ended_period_blocks_everything_after_its_start(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goal_periods.add(self.OPEN)

            with pytest.raises(GoalPeriodOverlapError):
                profile.goal_periods.add(GoalPeriod(date(2030, 1, 1), None, MaintenanceGoal()))

    def test_remove_deletes_the_period_that_starts_that_day(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.goal_periods.add(self.LOSS)
            profile.goal_periods.add(self.PERFORMANCE)

            profile.goal_periods.remove(D1)

            assert list(profile.goal_periods.list_periods()) == [self.PERFORMANCE]

    def test_removing_a_missing_period_is_an_error_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goal_periods.add(self.LOSS)

            with pytest.raises(GoalPeriodNotFoundError):
                profile.goal_periods.remove(date(2026, 1, 2))  # inside a period, not its start

            assert list(profile.goal_periods.list_periods()) == [self.LOSS]

    def test_the_slowest_and_fastest_loss_are_kept_exactly(self, harness: StorageHarness) -> None:
        slow = GoalPeriod(D1, D1, WeightLossGoal(100))
        fast = GoalPeriod(D2, D2, WeightLossGoal(1000))
        with closing(create(harness)) as profile:
            profile.goal_periods.add(slow)
            profile.goal_periods.add(fast)

            assert list(profile.goal_periods.list_periods()) == [slow, fast]

    def test_periods_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.goal_periods.add(self.LOSS)
        profile.goal_periods.add(self.OPEN)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert list(reopened.goal_periods.list_periods()) == [self.LOSS, self.OPEN]

    def test_a_cancelled_transaction_leaves_no_period(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.goal_periods.add(self.LOSS)
                raise RuntimeError("boom")

            assert profile.goal_periods.list_periods() == []
