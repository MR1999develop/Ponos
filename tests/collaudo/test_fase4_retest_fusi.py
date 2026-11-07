# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-06: the retest window with several real tests, and the local day of the test across
time zones and daylight-saving changes.

The history is the real SQLite one. The days are written out by hand: a failure shows the day, not
a computed value that could be wrong in the same way as the code.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.assessment.domain import (
    NoTest,
    NotYet,
    Overdue,
    Recommended,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
)
from ponos_coach.assessment.use_cases import EvaluateThresholdTest, NextRetest
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from tests.builders import fit_identity
from tests.collaudo.fase4_support import (
    START,
    ThresholdProfile,
    make_threshold_profile,
    run_fit,
    standard_legs,
)

LAST = date(2026, 3, 14)


@pytest.fixture
def tp(tmp_path: Path) -> Iterator[ThresholdProfile]:
    profile = make_threshold_profile(tmp_path)
    yield profile
    profile.close()


def a_test(number: int, day: date, bpm: int = 165) -> ThresholdTest:
    return ThresholdTest(fit_identity(number), day, datetime(2026, 1, 1, 7, tzinfo=UTC), bpm, 290)


def retest_on(tp: ThresholdProfile, today: date) -> object:
    return NextRetest(tp.history, lambda: today)()


# --- The retest window ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days_after", "expected"),
    [
        (0, NotYet(date(2026, 4, 25), date(2026, 5, 9))),
        (41, NotYet(date(2026, 4, 25), date(2026, 5, 9))),
        (42, Recommended(date(2026, 4, 25), date(2026, 5, 9))),
        (49, Recommended(date(2026, 4, 25), date(2026, 5, 9))),
        (56, Recommended(date(2026, 4, 25), date(2026, 5, 9))),
        (57, Overdue(1, date(2026, 5, 9))),
        (200, Overdue(144, date(2026, 5, 9))),
    ],
)
def test_t_piano_06_the_retest_window_is_from_the_42nd_to_the_56th_day_both_included(
    tp: ThresholdProfile, days_after: int, expected: object
) -> None:
    tp.history.add(a_test(1, LAST))

    assert retest_on(tp, LAST + timedelta(days=days_after)) == expected


def test_t_piano_06_without_any_test_the_first_one_is_due(tp: ThresholdProfile) -> None:
    assert retest_on(tp, LAST) == NoTest()


def test_t_piano_06_with_several_tests_only_the_latest_counts_whatever_the_order_of_entry(
    tp: ThresholdProfile,
) -> None:
    store = tp.history
    for number, day in ((3, date(2026, 2, 1)), (1, date(2026, 3, 14)), (2, date(2025, 12, 1))):
        store.add(a_test(number, day))

    assert [t.test_day for t in store.list_tests()] == [
        date(2025, 12, 1),
        date(2026, 2, 1),
        date(2026, 3, 14),
    ]
    assert retest_on(tp, date(2026, 4, 25)) == Recommended(date(2026, 4, 25), date(2026, 5, 9))


def test_t_piano_06_a_test_dated_after_today_is_not_yet_due_and_does_not_crash(
    tp: ThresholdProfile,
) -> None:
    tp.history.add(a_test(1, date(2026, 9, 1)))

    assert retest_on(tp, date(2026, 3, 1)) == NotYet(date(2026, 10, 13), date(2026, 10, 27))


def test_t_piano_06_two_tests_the_same_day_on_different_activities_are_both_kept(
    tp: ThresholdProfile,
) -> None:
    first = tp.evaluate_bytes(run_fit(standard_legs(), serial=1), "a.fit")
    second = tp.evaluate_bytes(run_fit(standard_legs(hr=170), serial=2), "b.fit")
    assert isinstance(first, ThresholdTestResult)
    assert isinstance(second, ThresholdTestResult)
    assert first.test_day == second.test_day

    tp.apply(first)
    tp.apply(second)  # the same day: the second replaces the threshold, both stay in the history

    assert sorted(t.threshold_bpm for t in tp.list_tests()) == [165, 170]
    assert [e.physiology.threshold.bpm for e in tp.entries() if e.valid_from == first.test_day] == [
        170
    ]


# --- The local day of the test ---------------------------------------------------------------


def day_of(
    tp: ThresholdProfile, start_utc: datetime, zone: str, serial: int
) -> date | ThresholdRejection | object:
    """The test day the use case gives for a run starting at `start_utc` in `zone`."""
    record = tp.import_bytes(run_fit(standard_legs(), start_utc, serial=serial), f"r{serial}.fit")
    evaluate = EvaluateThresholdTest(
        tp.profile.session.activities,
        tp.profile.archive,
        tp.profile.session.physiology,
        decode_stored_activity,
        timezone=ZoneInfo(zone),
    )
    outcome = evaluate(record.identity)
    assert isinstance(outcome, ThresholdTestResult), outcome
    return outcome.test_day


@pytest.mark.parametrize(
    ("zone", "utc", "local_day"),
    [
        # Rome, spring forward on 29 March 2026 at 01:00 UTC (02:00 -> 03:00)
        ("Europe/Rome", datetime(2026, 3, 28, 22, 59, 59, tzinfo=UTC), date(2026, 3, 28)),
        ("Europe/Rome", datetime(2026, 3, 28, 23, 0, 0, tzinfo=UTC), date(2026, 3, 29)),
        ("Europe/Rome", datetime(2026, 3, 29, 0, 59, 59, tzinfo=UTC), date(2026, 3, 29)),
        ("Europe/Rome", datetime(2026, 3, 29, 1, 0, 0, tzinfo=UTC), date(2026, 3, 29)),
        ("Europe/Rome", datetime(2026, 3, 29, 21, 59, 59, tzinfo=UTC), date(2026, 3, 29)),
        ("Europe/Rome", datetime(2026, 3, 29, 22, 0, 0, tzinfo=UTC), date(2026, 3, 30)),
        # Rome, fall back on 25 October 2026 at 01:00 UTC (03:00 -> 02:00)
        ("Europe/Rome", datetime(2026, 10, 24, 21, 59, 59, tzinfo=UTC), date(2026, 10, 24)),
        ("Europe/Rome", datetime(2026, 10, 24, 22, 0, 0, tzinfo=UTC), date(2026, 10, 25)),
        ("Europe/Rome", datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC), date(2026, 10, 25)),
        ("Europe/Rome", datetime(2026, 10, 25, 1, 0, 0, tzinfo=UTC), date(2026, 10, 25)),
        ("Europe/Rome", datetime(2026, 10, 25, 22, 59, 59, tzinfo=UTC), date(2026, 10, 25)),
        ("Europe/Rome", datetime(2026, 10, 25, 23, 0, 0, tzinfo=UTC), date(2026, 10, 26)),
        # Los Angeles (UTC-7 in March 2026) and Auckland (UTC+13 in March 2026)
        ("America/Los_Angeles", datetime(2026, 3, 14, 6, 59, 59, tzinfo=UTC), date(2026, 3, 13)),
        ("America/Los_Angeles", datetime(2026, 3, 14, 7, 0, 0, tzinfo=UTC), date(2026, 3, 14)),
        ("Pacific/Auckland", datetime(2026, 3, 14, 10, 59, 59, tzinfo=UTC), date(2026, 3, 14)),
        ("Pacific/Auckland", datetime(2026, 3, 14, 11, 0, 0, tzinfo=UTC), date(2026, 3, 15)),
        # an hour of 30 minutes' offset and the +14 zone
        ("Asia/Kolkata", datetime(2026, 3, 14, 18, 29, 59, tzinfo=UTC), date(2026, 3, 14)),
        ("Asia/Kolkata", datetime(2026, 3, 14, 18, 30, 0, tzinfo=UTC), date(2026, 3, 15)),
        ("Pacific/Kiritimati", datetime(2026, 3, 13, 9, 59, 59, tzinfo=UTC), date(2026, 3, 13)),
        ("Pacific/Kiritimati", datetime(2026, 3, 13, 10, 0, 0, tzinfo=UTC), date(2026, 3, 14)),
    ],
)
def test_t_piano_06_the_test_day_is_the_local_day_of_the_start_of_the_activity(
    tp: ThresholdProfile, zone: str, utc: datetime, local_day: date
) -> None:
    assert day_of(tp, utc, zone, serial=1) == local_day


def test_t_piano_06_a_run_that_crosses_midnight_belongs_to_the_day_it_started(
    tp: ThresholdProfile,
) -> None:
    # starts 23:50 in Rome on 14 March, the 30 minutes are after midnight
    start = datetime(2026, 3, 14, 22, 50, tzinfo=UTC)

    assert day_of(tp, start, "Europe/Rome", serial=1) == date(2026, 3, 14)


def test_t_piano_06_the_hr_limits_used_are_those_of_the_local_day_not_of_the_utc_day(
    tp: ThresholdProfile,
) -> None:
    # from 15 March the max HR is 165: a threshold of 165 is not below it from that day
    narrow = Physiology(HeartRateLimits(50, 165), ThresholdHeartRate(150), Sex.MALE)
    tp.profile.session.physiology.put(PhysiologyEntry(date(2026, 3, 15), narrow))
    late_evening_14 = datetime(2026, 3, 14, 22, 30, tzinfo=UTC)  # 23:30 on the 14th in Rome
    just_after_midnight = datetime(2026, 3, 14, 23, 30, tzinfo=UTC)  # 00:30 on the 15th in Rome

    ok = tp.evaluate_bytes(run_fit(standard_legs(), late_evening_14, serial=1), "a.fit")
    narrow_day = tp.evaluate_bytes(run_fit(standard_legs(), just_after_midnight, serial=2), "b.fit")

    assert isinstance(ok, ThresholdTestResult)
    assert ok.test_day == date(2026, 3, 14)
    assert narrow_day is ThresholdRejection.THRESHOLD_OUT_OF_RANGE


def test_t_piano_06_the_start_instant_saved_with_the_test_is_utc(tp: ThresholdProfile) -> None:
    outcome = tp.evaluate_bytes(run_fit(standard_legs(), START, serial=1))
    assert isinstance(outcome, ThresholdTestResult)

    applied = tp.apply(outcome)

    assert applied.test.segment_start.utcoffset() == timedelta(0)
    assert applied.test.segment_start == datetime(2026, 3, 14, 7, 10, tzinfo=UTC)
    [stored] = tp.list_tests()
    assert stored == applied.test
