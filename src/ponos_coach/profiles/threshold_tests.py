# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases that apply a threshold test to the profile and take it back.

They live with the profile because they also write a dated measurement, through the same
`UpdateMeasurements` rules (validity until the next entry, HR consistency, no dates in the future)
without repeating them.

- `ApplyThresholdTest`: saves the test in the history and the threshold as a measurement dated on
  the test day, in a single transaction.
- `RemoveThresholdTest`: the undo; removes the test and the measurement it made, putting the
  profile's parameters back exactly as they were.

The loads depend on the threshold through the fingerprint of their parameters, so they are
recomputed on their own at the next overview, as for any change of the measurements. Health data:
never in logs or error messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.assessment.domain import ThresholdTest, ThresholdTestResult
from ponos_coach.assessment.ports import ThresholdTestAlreadyStoredError, ThresholdTestStore
from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.profiles.measurements import MeasurementUpdate, UpdateMeasurements, ValidityPeriod


class ThresholdTestChangedError(Exception):
    """The measurement the test made was changed after it: it is not undone, nothing changes."""


def _entry_on(physiology: PhysiologyStore, day: date) -> PhysiologyEntry | None:
    return next((e for e in physiology.list_entries() if e.valid_from == day), None)


@dataclass(frozen=True, slots=True)
class AppliedThresholdTest:
    """What applying a test did, and what undoing it needs.

    `test` is the history entry; `period` says from when to when the new threshold holds;
    `written` is the physiological entry the test created on its day and `replaced` the one that
    was on that day before (None if the day had none).
    """

    test: ThresholdTest
    period: ValidityPeriod
    written: PhysiologyEntry
    replaced: PhysiologyEntry | None


class ApplyThresholdTest:
    """Applies an accepted test: history entry and dated threshold, all or nothing."""

    def __init__(
        self,
        activities: ActivityIndex,
        tests: ThresholdTestStore,
        physiology: PhysiologyStore,
        measurements: UpdateMeasurements,
    ) -> None:
        self._activities = activities
        self._tests = tests
        self._physiology = physiology
        self._measurements = measurements

    def __call__(self, result: ThresholdTestResult) -> AppliedThresholdTest:
        """Saves the test and sets the threshold HR from the test day on (until the next entry).

        Raises:
            ThresholdTestAlreadyStoredError: this activity is already a test; nothing changes.
            InvalidMeasurementsError: day in the future or too old, or HR inconsistent with the
                limits in force; nothing was written.
            MissingPhysiologyError: the profile has no physiological parameters to rely on.
            AssessmentStoreError, LoadStoreError, ProfileDataError: write failed; nothing changes.
        """
        with self._activities.transaction():
            if self._tests.get(result.identity) is not None:
                raise ThresholdTestAlreadyStoredError("the activity is already a test")
            replaced = _entry_on(self._physiology, result.test_day)
            saved = self._measurements.save_in_open_transaction(
                MeasurementUpdate(result.test_day, threshold_bpm=result.threshold_bpm)
            )
            written = _entry_on(self._physiology, result.test_day)
            period = saved.heart_rate
            if written is None or period is None:
                raise RuntimeError("the measurement just saved is not there")  # an invariant
            test = ThresholdTest.of(result)
            self._tests.add(test)
        return AppliedThresholdTest(test, period, written, replaced)


class RemoveThresholdTest:
    """Takes an applied test back (the undo), in a single transaction."""

    def __init__(
        self, activities: ActivityIndex, tests: ThresholdTestStore, physiology: PhysiologyStore
    ) -> None:
        self._activities = activities
        self._tests = tests
        self._physiology = physiology

    def __call__(self, applied: AppliedThresholdTest) -> None:
        """Removes the test and brings the physiological entry of its day back as it was.

        Raises:
            ThresholdTestChangedError: the entry of that day is no longer the one the test wrote
                (the user edited it since); nothing changes.
            ThresholdTestNotFoundError: the test is no longer in the history; nothing changes.
            AssessmentStoreError, LoadStoreError: write failed; nothing changes.
        """
        day = applied.test.test_day
        with self._activities.transaction():
            if _entry_on(self._physiology, day) != applied.written:
                raise ThresholdTestChangedError("the measurement was changed after the test")
            self._tests.remove(applied.test.identity)
            if applied.replaced is None:
                self._physiology.remove(day)
            else:
                self._physiology.put(applied.replaced)
