# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`EvaluateThresholdTest`, `ListThresholdTests` and `NextRetest` against an in-memory profile."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ponos_coach.activities.ports import ActivityNotFoundError
from ponos_coach.assessment.domain import (
    NotYet,
    Overdue,
    Recommended,
    SegmentSelection,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    MissingPhysiologyError,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.load.use_cases import ExcludedActivity, ExclusionReason
from tests.assessment.world import TEST_DAY, ThresholdWorld
from tests.builders import fit_identity
from tests.load.test_activity_detail import add_activity

START = datetime(2026, 1, 20, 7, 0, tzinfo=UTC)


def a_test(number: int, day: date, bpm: int = 166, pace: int | None = 290) -> ThresholdTest:
    return ThresholdTest(fit_identity(number), day, START, bpm, pace)


def test_the_chosen_activity_is_evaluated_with_the_local_day_and_the_limits_of_that_day() -> None:
    world = ThresholdWorld()
    record = world.add_fixture("run_threshold_test.fit")

    result = world.evaluate(record.identity)

    assert isinstance(result, ThresholdTestResult)
    assert result.identity == record.identity
    assert result.test_day == TEST_DAY
    assert result.selection is SegmentSelection.BY_DISTANCE
    assert (result.threshold_bpm, result.pace_s_per_km) == (168, 278)
    assert result.hr_zone_starts_bpm == pytest.approx((168 * 0.85, 168 * 0.9, 168 * 0.95, 168.0))


def test_the_local_day_is_the_day_in_the_profile_time_zone() -> None:
    # 23:30 UTC on 14 March is 00:30 on 15 March in Rome
    world = ThresholdWorld()
    record = add_activity(
        world, [170] * 2400, serial=3, start=datetime(2026, 3, 14, 23, 30, tzinfo=UTC)
    )

    result = world.evaluate(record.identity)

    assert isinstance(result, ThresholdTestResult)
    assert result.test_day == date(2026, 3, 15)


def test_the_hr_limits_are_those_valid_on_the_day_of_the_test() -> None:
    world = ThresholdWorld()
    record = world.add_fixture("run_threshold_test.fit")
    # From 1 March the maximum is 160 bpm: the 168 bpm of the test are out of the limits
    lowered = Physiology(HeartRateLimits(50, 160), ThresholdHeartRate(150), Sex.MALE)
    world.profile.physiology.put(PhysiologyEntry(date(2026, 3, 1), lowered))

    outcome = world.evaluate(record.identity)

    assert outcome is ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES


def test_a_rejected_activity_gives_its_reason() -> None:
    world = ThresholdWorld()
    record = world.add_fixture("run_threshold_paused.fit")

    assert world.evaluate(record.identity) is ThresholdRejection.TOO_MANY_PAUSES


def test_evaluating_saves_nothing() -> None:
    world = ThresholdWorld()
    record = world.add_fixture("run_threshold_test.fit")
    entries_before = world.entries()

    world.evaluate(record.identity)

    assert world.entries() == entries_before
    assert world.list_tests() == ()


def test_an_activity_not_in_the_index_is_an_error() -> None:
    with pytest.raises(ActivityNotFoundError):
        ThresholdWorld().evaluate(fit_identity(99))


def test_a_profile_without_physiological_parameters_is_an_explicit_error() -> None:
    world = ThresholdWorld(with_physiology=False)
    record = world.add_fixture("run_threshold_test.fit")

    with pytest.raises(MissingPhysiologyError):
        world.evaluate(record.identity)


def test_a_file_that_is_no_longer_in_the_archive_gives_the_reason_not_an_error() -> None:
    world = ThresholdWorld()
    record = world.add_fixture("run_threshold_test.fit")
    world.archive.files.clear()

    assert world.evaluate(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.FILE_MISSING
    )


def test_the_history_is_listed_by_day_and_the_retest_follows_the_last_one() -> None:
    world = ThresholdWorld()
    assert world.list_tests() == ()
    world.profile.threshold_tests.add(a_test(2, date(2026, 2, 1)))
    world.profile.threshold_tests.add(a_test(1, date(2026, 1, 20)))

    assert [t.test_day for t in world.list_tests()] == [date(2026, 1, 20), date(2026, 2, 1)]
    # last test 1 February; today is 20 March, 47 days later: inside the 42-56 day window
    assert world.next_retest() == Recommended(date(2026, 3, 15), date(2026, 3, 29))


def test_the_retest_is_not_yet_due_right_after_a_test() -> None:
    world = ThresholdWorld()
    world.profile.threshold_tests.add(a_test(1, date(2026, 3, 10)))

    assert world.next_retest() == NotYet(date(2026, 4, 21), date(2026, 5, 5))


def test_the_retest_is_overdue_long_after_the_last_test() -> None:
    world = ThresholdWorld()
    world.profile.threshold_tests.add(a_test(1, date(2026, 1, 1)))

    assert world.next_retest() == Overdue(22, date(2026, 2, 26))
