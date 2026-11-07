# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ApplyThresholdTest` and `RemoveThresholdTest`: history and dated threshold, all or nothing."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ponos_coach.activities.domain import ActivityIdentity, ActivityRecord
from ponos_coach.assessment.domain import ThresholdTest, ThresholdTestResult
from ponos_coach.assessment.ports import (
    AssessmentStoreError,
    ThresholdTestAlreadyStoredError,
    ThresholdTestNotFoundError,
    ThresholdTestStore,
)
from ponos_coach.load.domain import (
    PhysiologyEntry,
    ThresholdHeartRate,
    load_fingerprint,
    physiology_for_day,
)
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementProblem,
    ValidityPeriod,
)
from ponos_coach.profiles.threshold_tests import ApplyThresholdTest, ThresholdTestChangedError
from tests.assessment.world import FIRST_DAY, TEST_DAY, TODAY, ThresholdWorld
from tests.builders import physiology_entry


def evaluated(world: ThresholdWorld, name: str = "run_threshold_test.fit") -> ThresholdTestResult:
    record = world.add_fixture(name)
    result = world.evaluate(record.identity)
    assert isinstance(result, ThresholdTestResult)
    return result


def threshold_on(world: ThresholdWorld, day: date) -> int:
    return physiology_for_day(world.entries(), day).threshold.bpm


def test_applying_a_test_saves_it_and_sets_the_threshold_from_the_test_day_on() -> None:
    world = ThresholdWorld()
    result = evaluated(world)

    applied = world.apply(result)

    assert applied.test == ThresholdTest.of(result)
    assert world.list_tests() == (applied.test,)
    assert applied.period == ValidityPeriod(TEST_DAY, None)
    assert threshold_on(world, TEST_DAY) == 168
    assert threshold_on(world, date(2026, 3, 13)) == 170  # the day before: unchanged
    assert threshold_on(world, TODAY) == 168


def test_the_new_entry_keeps_the_limits_and_the_sex_in_force_that_day() -> None:
    world = ThresholdWorld()
    before = physiology_for_day(world.entries(), TEST_DAY)

    world.apply(evaluated(world))

    after = physiology_for_day(world.entries(), TEST_DAY)
    assert after.limits == before.limits
    assert after.sex == before.sex
    assert after.threshold == ThresholdHeartRate(168)


def test_a_test_before_a_later_entry_holds_only_until_that_entry() -> None:
    world = ThresholdWorld()
    world.profile.physiology.put(physiology_entry(date(2026, 3, 18), 172))

    applied = world.apply(evaluated(world))

    assert applied.period == ValidityPeriod(TEST_DAY, date(2026, 3, 17))
    assert threshold_on(world, date(2026, 3, 17)) == 168
    assert threshold_on(world, date(2026, 3, 18)) == 172


def test_a_test_on_the_day_of_an_entry_replaces_it_and_undoing_it_brings_the_old_one_back() -> None:
    world = ThresholdWorld()
    old = physiology_entry(TEST_DAY, 171)
    world.profile.physiology.put(old)
    entries_before = world.entries()

    applied = world.apply(evaluated(world))

    assert applied.replaced == old
    assert threshold_on(world, TEST_DAY) == 168
    world.remove(applied)
    assert world.entries() == entries_before  # exactly the state before
    assert world.list_tests() == ()


def test_undoing_a_test_that_made_a_new_entry_removes_it() -> None:
    world = ThresholdWorld()
    entries_before = world.entries()
    applied = world.apply(evaluated(world))
    assert applied.replaced is None

    world.remove(applied)

    assert world.entries() == entries_before
    assert threshold_on(world, TODAY) == 170


def test_the_loads_depend_on_the_threshold_through_the_fingerprint_so_they_are_recomputed() -> None:
    world = ThresholdWorld()
    weights = world.profile.sport_weights.load()
    day = TEST_DAY
    fingerprint_before = load_fingerprint(physiology_for_day(world.entries(), day), weights)

    applied = world.apply(evaluated(world))
    fingerprint_applied = load_fingerprint(physiology_for_day(world.entries(), day), weights)
    world.remove(applied)
    fingerprint_removed = load_fingerprint(physiology_for_day(world.entries(), day), weights)

    assert fingerprint_applied != fingerprint_before
    assert fingerprint_removed == fingerprint_before


def test_the_same_activity_cannot_be_applied_twice_and_nothing_changes() -> None:
    world = ThresholdWorld()
    result = evaluated(world)
    world.apply(result)
    entries_after_first = world.entries()

    with pytest.raises(ThresholdTestAlreadyStoredError):
        world.apply(replace(result, threshold_bpm=171))

    assert world.entries() == entries_after_first
    assert len(world.list_tests()) == 1


def test_a_failure_saving_the_history_leaves_the_threshold_as_it_was() -> None:
    world = ThresholdWorld()
    result = evaluated(world)
    entries_before = world.entries()

    class FailingHistory:
        def __init__(self, inner: ThresholdTestStore) -> None:
            self._inner = inner

        def get(self, identity: ActivityIdentity) -> ThresholdTest | None:
            return self._inner.get(identity)

        def add(self, test: ThresholdTest) -> None:
            del test
            raise AssessmentStoreError("disk full")

    failing = ApplyThresholdTest(
        world.profile.activities,
        FailingHistory(world.profile.threshold_tests),  # type: ignore[arg-type]
        world.profile.physiology,
        world.measurements,
    )

    with pytest.raises(AssessmentStoreError):
        failing(result)

    assert world.entries() == entries_before
    assert world.list_tests() == ()


def test_a_test_from_a_future_day_is_refused_with_the_measurement_rule() -> None:
    world = ThresholdWorld()
    result = replace(evaluated(world), test_day=date(2026, 3, 21))  # today is 20 March

    with pytest.raises(InvalidMeasurementsError) as caught:
        world.apply(result)

    assert caught.value.problem is MeasurementProblem.DATE_IN_FUTURE
    assert world.list_tests() == ()
    assert world.entries() == [physiology_entry(FIRST_DAY, 170)]


def test_a_threshold_that_no_longer_fits_the_limits_in_force_is_refused() -> None:
    world = ThresholdWorld()
    result = replace(evaluated(world), threshold_bpm=195)  # above the 190 bpm maximum

    with pytest.raises(InvalidMeasurementsError) as caught:
        world.apply(result)

    assert caught.value.problem is MeasurementProblem.INVALID_HEART_RATE
    assert world.list_tests() == ()


def test_undoing_is_refused_if_the_measurement_was_edited_after_the_test() -> None:
    world = ThresholdWorld()
    applied = world.apply(evaluated(world))
    edited = PhysiologyEntry(applied.written.valid_from, physiology_entry(TEST_DAY, 166).physiology)
    world.profile.physiology.put(edited)

    with pytest.raises(ThresholdTestChangedError):
        world.remove(applied)

    assert world.list_tests() == (applied.test,)
    assert world.entries()[-1] == edited


def test_undoing_twice_is_refused() -> None:
    world = ThresholdWorld()
    applied = world.apply(evaluated(world))
    world.remove(applied)

    with pytest.raises((ThresholdTestChangedError, ThresholdTestNotFoundError)):
        world.remove(applied)


def test_two_tests_on_different_days_each_set_their_own_threshold() -> None:
    world = ThresholdWorld()
    first = world.apply(evaluated(world, "run_threshold_test.fit"))
    other: ActivityRecord = world.add_fixture("run_threshold_no_distance.fit")
    second_result = world.evaluate(other.identity)
    assert isinstance(second_result, ThresholdTestResult)

    second = world.apply(replace(second_result, test_day=date(2026, 3, 18), threshold_bpm=166))

    assert threshold_on(world, date(2026, 3, 17)) == 168
    assert threshold_on(world, date(2026, 3, 18)) == 166
    assert [t.test_day for t in world.list_tests()] == [TEST_DAY, date(2026, 3, 18)]
    world.remove(second)
    assert threshold_on(world, date(2026, 3, 18)) == 168
    assert world.list_tests() == (first.test,)
