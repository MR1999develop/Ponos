# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Applying and taking back a threshold test on a real profile (SQLite, on-disk archive).

The state is compared exactly before and after: the physiological entries, the history and, with
the profile closed, the rows of the tables. The interruptions are real: a worker process dies
(`os._exit`) inside the transaction, at each of the points where a partial write could survive.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Activity, FileFormat
from ponos_coach.assessment.domain import (
    NoTest,
    Overdue,
    SegmentSelection,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
)
from ponos_coach.assessment.ports import (
    AssessmentStoreError,
    ThresholdTestAlreadyStoredError,
    ThresholdTestStore,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementProblem,
    MeasurementUpdate,
)
from ponos_coach.profiles.threshold_tests import (
    AppliedThresholdTest,
    ApplyThresholdTest,
    ThresholdTestChangedError,
)
from tests.builders import physiology_entry
from tests.collaudo.fase4_support import (
    DAY,
    FIRST_DAY,
    THRESHOLD_BEFORE,
    ThresholdProfile,
    make_threshold_profile,
    run_fit,
    standard_legs,
    wire,
)
from tests.collaudo.support import create_profile
from tests.collaudo.test_database_del_profilo import rows

WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase4_worker.py"
)
DIED = 77
TABLES = ("physiology", "threshold_test", "load_cache", "activities")


@pytest.fixture
def tp(tmp_path: Path) -> Iterator[ThresholdProfile]:
    profile = make_threshold_profile(tmp_path)
    yield profile
    profile.close()


def a_result(
    tp: ThresholdProfile, name: str = "run.fit", serial: int = 1_000_001
) -> ThresholdTestResult:
    outcome = tp.evaluate_bytes(run_fit(standard_legs(), serial=serial), name)
    assert isinstance(outcome, ThresholdTestResult)
    return outcome


def state(tp: ThresholdProfile) -> tuple[list[PhysiologyEntry], tuple[ThresholdTest, ...]]:
    return tp.entries(), tp.list_tests()


def entry(day: date, *, low: int = 50, high: int = 190, threshold: int = 170) -> PhysiologyEntry:
    return PhysiologyEntry(
        day, Physiology(HeartRateLimits(low, high), ThresholdHeartRate(threshold), Sex.FEMALE)
    )


# --- Exact state before and after -------------------------------------------------------------


def test_applying_a_test_writes_the_history_and_the_dated_threshold_and_nothing_else(
    tp: ThresholdProfile,
) -> None:
    result = a_result(tp)
    entries_before, _ = state(tp)

    applied = tp.apply(result)

    assert tp.list_tests() == (ThresholdTest.of(result),)
    assert tp.entries() == sorted(
        [*entries_before, entry_with_threshold(DAY, 165)], key=lambda e: e.valid_from
    )
    assert applied.written == entry_with_threshold(DAY, 165)
    assert applied.replaced is None
    # valid until the day before the entry `create_profile` made on 1 July
    assert (applied.period.valid_from, applied.period.valid_to) == (DAY, date(2026, 6, 30))
    assert tp.next_retest() == Overdue(days=53, last_day=date(2026, 5, 9))  # 1 July is day 109


def entry_with_threshold(day: date, bpm: int) -> PhysiologyEntry:
    """What the test writes: the limits and the sex in force that day, the new threshold."""
    return physiology_entry(day, bpm)  # 50-190 bpm, male: the parameters of the profile


def test_undoing_a_test_that_made_a_new_entry_brings_the_exact_state_back(
    tp: ThresholdProfile,
) -> None:
    before = state(tp)
    applied = tp.apply(a_result(tp))

    tp.remove(applied)

    assert state(tp) == before
    assert tp.next_retest() == NoTest()


def test_undoing_a_test_that_replaced_a_user_entry_gives_that_entry_back(
    tp: ThresholdProfile,
) -> None:
    tp.profile.session.physiology.put(entry(DAY, low=55, high=188, threshold=160))
    before = state(tp)
    applied = tp.apply(a_result(tp))

    assert applied.replaced == entry(DAY, low=55, high=188, threshold=160)
    assert [e for e in tp.entries() if e.valid_from == DAY] == [
        PhysiologyEntry(
            DAY,
            Physiology(HeartRateLimits(55, 188), ThresholdHeartRate(165), Sex.FEMALE),
        )
    ]  # the limits and the sex of the replaced entry are kept
    tp.remove(applied)

    assert state(tp) == before


def test_a_later_entry_of_the_user_is_neither_touched_nor_overridden(
    tp: ThresholdProfile,
) -> None:
    later = date(2026, 5, 1)
    tp.profile.session.physiology.put(entry(later, threshold=172))
    before = state(tp)

    applied = tp.apply(a_result(tp))

    assert applied.period.valid_to == date(2026, 4, 30)
    assert [e for e in tp.entries() if e.valid_from == later] == [entry(later, threshold=172)]
    tp.remove(applied)
    assert state(tp) == before


def test_the_same_activity_cannot_be_applied_twice_and_the_state_does_not_change(
    tp: ThresholdProfile,
) -> None:
    result = a_result(tp)
    tp.apply(result)
    after_first = state(tp)

    with pytest.raises(ThresholdTestAlreadyStoredError):
        tp.apply(result)

    assert state(tp) == after_first


def test_a_test_edited_by_the_user_afterwards_is_not_undone_and_nothing_changes(
    tp: ThresholdProfile,
) -> None:
    applied = tp.apply(a_result(tp))
    tp.measurements(MeasurementUpdate(DAY, threshold_bpm=171))
    after_edit = state(tp)

    with pytest.raises(ThresholdTestChangedError):
        tp.remove(applied)

    assert state(tp) == after_edit
    assert len(tp.list_tests()) == 1


def test_undoing_twice_is_refused_and_leaves_the_state_alone(tp: ThresholdProfile) -> None:
    before = state(tp)
    applied = tp.apply(a_result(tp))
    tp.remove(applied)

    with pytest.raises(ThresholdTestChangedError):
        tp.remove(applied)

    assert state(tp) == before


def test_a_test_of_a_day_after_today_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    profile = create_profile(tmp_path)
    profile.session.physiology.put(physiology_entry(FIRST_DAY, THRESHOLD_BEFORE))
    wired = wire(profile, today=date(2026, 3, 10))  # the test of 14 March is in the future
    try:
        result = a_result(wired)
        before = state(wired)

        with pytest.raises(InvalidMeasurementsError) as raised:
            wired.apply(result)

        assert raised.value.problem is MeasurementProblem.DATE_IN_FUTURE
        assert state(wired) == before
    finally:
        wired.close()


def test_applying_to_a_profile_without_parameters_writes_nothing(tmp_path: Path) -> None:
    profile = create_profile(tmp_path)
    wired = wire(profile)
    try:
        result = ThresholdTestResult(
            wired.import_bytes(run_fit(standard_legs())).identity,
            DAY,
            SegmentSelection.BY_DISTANCE,
            600.0,
            datetime(2026, 3, 14, 7, 10, tzinfo=UTC),
            165,
            286,
            1200,
            (140.25, 148.5, 156.75, 165.0),
            (366.0, 325.0, 304.0, 286.0),
        )
        # `create_profile` gives a first entry; remove it to have none at all
        for existing in wired.entries():
            wired.profile.session.physiology.remove(existing.valid_from)

        with pytest.raises(Exception, match=r".+") as raised:
            wired.apply(result)

        assert type(raised.value).__name__ == "MissingPhysiologyError"
        assert wired.list_tests() == ()
        assert wired.entries() == []
    finally:
        wired.close()


# --- Failures inside the transaction, in process ----------------------------------------------


class FailingHistory:
    """The real history store whose `add` fails after the measurement was already written."""

    def __init__(self, inner: ThresholdTestStore, failure: BaseException) -> None:
        self._inner = inner
        self._failure = failure

    def add(self, test: ThresholdTest) -> None:  # noqa: ARG002
        raise self._failure

    def remove(self, identity: object) -> None:
        self._inner.remove(identity)  # type: ignore[arg-type]

    def get(self, identity: object) -> ThresholdTest | None:
        return self._inner.get(identity)  # type: ignore[arg-type]

    def list_tests(self) -> object:
        return self._inner.list_tests()


@pytest.mark.parametrize("failure", [AssessmentStoreError("disk"), KeyboardInterrupt()])
def test_a_failure_after_the_measurement_was_written_rolls_the_measurement_back(
    tp: ThresholdProfile, failure: BaseException
) -> None:
    result = a_result(tp)
    before = state(tp)
    session = tp.profile.session
    broken = ApplyThresholdTest(
        session.activities,
        FailingHistory(tp.history, failure),  # type: ignore[arg-type]
        session.physiology,
        tp.measurements,
    )

    with pytest.raises(type(failure)):
        broken(result)

    assert state(tp) == before
    tp.apply(result)  # and the profile still works
    assert len(tp.list_tests()) == 1


# --- The process dies in the middle ----------------------------------------------------------


def snapshot(folder: Path) -> dict[str, list[tuple[object, ...]]]:
    return {table: rows(folder / PROFILE_DATABASE_NAME, table) for table in TABLES}


def run_worker(folder: Path, mode: str) -> int:
    done = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-I", str(WORKER), str(folder), mode],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode in {0, DIED}, done.stderr
    return done.returncode


def profile_with_a_test_activity(base: Path) -> Path:
    tp = make_threshold_profile(base)
    try:
        tp.import_bytes(run_fit(standard_legs()))
        return tp.folder
    finally:
        tp.close()


@pytest.mark.parametrize("mode", ["apply-before-history", "apply-after-history"])
def test_a_process_killed_in_the_middle_of_applying_leaves_no_partial_write(
    tmp_path: Path, mode: str
) -> None:
    folder = profile_with_a_test_activity(tmp_path)
    before = snapshot(folder)

    assert run_worker(folder, mode) == DIED

    assert snapshot(folder) == before  # neither the threshold nor the history survived
    assert run_worker(folder, "apply-complete") == 0  # and the next attempt succeeds
    after = snapshot(folder)
    assert len(after["threshold_test"]) == 1
    assert len(after["physiology"]) == len(before["physiology"]) + 1


@pytest.mark.parametrize("mode", ["remove-after-history", "remove-after-measurement"])
def test_a_process_killed_in_the_middle_of_undoing_leaves_the_test_applied(
    tmp_path: Path, mode: str
) -> None:
    folder = profile_with_a_test_activity(tmp_path)
    assert run_worker(folder, "apply-complete") == 0
    applied = snapshot(folder)

    assert run_worker(folder, mode) == DIED

    assert snapshot(folder) == applied  # still a whole applied test, not half undone


# --- The load follows the threshold, consistently ----------------------------------------------


class CountingDecode:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, data: bytes, file_format: FileFormat) -> Activity:
        self.calls += 1
        return decode_stored_activity(data, file_format)


def loads(overview: ProfileOverview) -> list[tuple[object, ...]]:
    return [(a.identity, a.load.trimp_eq, a.load.zone_seconds.values) for a in overview.activities]


def test_the_load_is_recomputed_after_applying_and_after_undoing_and_is_consistent(
    tp: ThresholdProfile, tmp_path: Path
) -> None:
    result = a_result(tp)
    decode = CountingDecode()
    original = tp.profile.overview(decode)
    assert decode.calls == 1
    tp.profile.overview(decode)
    assert decode.calls == 1  # served from the cache

    applied = tp.apply(result)
    after = tp.profile.overview(decode)

    assert decode.calls == 2  # the threshold changed: recomputed
    assert loads(after) != loads(original)
    # The oracle: a profile that had the threshold 165 from the test day from the start.
    oracle = make_threshold_profile(tmp_path / "oracle")
    try:
        oracle.import_bytes(run_fit(standard_legs()))
        oracle.profile.session.physiology.put(physiology_entry(DAY, 165))
        assert loads(oracle.profile.overview()) == loads(after)
    finally:
        oracle.close()

    tp.remove(applied)
    restored = tp.profile.overview(decode)

    assert loads(restored) == loads(original)
    assert restored.day_loads() == original.day_loads()


def test_rejected_tests_never_reach_apply(tp: ThresholdProfile) -> None:
    entries_before = tp.entries()
    outcome = tp.evaluate_bytes(run_fit(standard_legs(hr=190)))

    assert outcome is ThresholdRejection.THRESHOLD_OUT_OF_RANGE
    assert tp.list_tests() == ()
    assert tp.entries() == entries_before


def test_applied_value_object_carries_what_the_undo_needs(tp: ThresholdProfile) -> None:
    applied = tp.apply(a_result(tp))

    assert isinstance(applied, AppliedThresholdTest)
    assert applied.test.threshold_bpm == 165
