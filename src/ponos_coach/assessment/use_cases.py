# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the threshold test that need no profile data beyond the history.

- `EvaluateThresholdTest`: reads the activity the user chose and says what the test gives, or why
  it is rejected. It saves nothing.
- `ListThresholdTests` and `NextRetest`: the history and when to repeat the test.

Applying and removing a test also touch the measurements, so they live with the profile
(`profiles.ApplyThresholdTest`, `profiles.RemoveThresholdTest`). Health data: never in logs or
error messages.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from zoneinfo import ZoneInfo

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES, ActivityIdentity
from ponos_coach.activities.ports import ActivityArchive, ActivityIndex, ActivityNotFoundError
from ponos_coach.assessment.domain import (
    DEFAULT_PROTOCOL,
    RetestStatus,
    ThresholdProtocol,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
    evaluate_threshold_test,
    retest_status,
)
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.load.domain import physiology_for_day
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.load.use_cases import (
    Decoder,
    ExcludedActivity,
    ExclusionReason,
    local_day_of,
    read_original_activity,
)

type ThresholdEvaluation = ThresholdTestResult | ThresholdRejection | ExcludedActivity
"""What evaluating an activity gives: the result, the reason it is rejected as a test, or the
reason its file cannot be read."""


class EvaluateThresholdTest:
    """Evaluates an activity of the open profile as a threshold test (30 or 20 minutes).

    Dependencies: index, archive, physiological parameters, `decode` (from the Composition Root)
    and the profile's time zone. It only reads.
    """

    def __init__(
        self,
        index: ActivityIndex,
        archive: ActivityArchive,
        physiology: PhysiologyStore,
        decode: Decoder,
        *,
        timezone: ZoneInfo,
        max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES,
    ) -> None:
        if max_file_bytes <= 0:
            raise ValueError("the byte cap must be positive")
        self._index = index
        self._archive = archive
        self._physiology = physiology
        self._decode = decode
        self._timezone = timezone
        self._max_file_bytes = max_file_bytes

    def __call__(
        self, activity: ActivityIdentity, protocol: ThresholdProtocol = DEFAULT_PROTOCOL
    ) -> ThresholdEvaluation:
        """The result for the chosen protocol, 30 or 20 minutes (segment, threshold HR and pace, HR
        and pace zones, number of samples), the reason the activity is not valid as a test, or the
        reason its file cannot be used.

        The HR limits are those valid on the activity's local day.

        Raises:
            ActivityNotFoundError: the identity is not in the index.
            MissingPhysiologyError: no physiological parameter set.
            LoadStoreError: the parameters cannot be read.
        """
        record = self._index.get(activity)
        if record is None:
            raise ActivityNotFoundError("activity not in the index")
        original = read_original_activity(self._archive, record, self._decode, self._max_file_bytes)
        if isinstance(original, ExclusionReason):
            return ExcludedActivity(activity, original)
        day = local_day_of(record.start, self._timezone)
        physiology = physiology_for_day(self._physiology.list_entries(), day)
        return evaluate_threshold_test(original, physiology.limits, day, protocol)


class ListThresholdTests:
    """The history of the applied tests of the open profile."""

    def __init__(self, tests: ThresholdTestStore) -> None:
        self._tests = tests

    def __call__(self) -> tuple[ThresholdTest, ...]:
        """The tests by ascending day.

        Raises:
            AssessmentStoreError: unreadable data.
        """
        return tuple(self._tests.list_tests())


class NextRetest:
    """When to repeat the test: nothing yet, not yet, recommended from a day to a day, overdue."""

    def __init__(self, tests: ThresholdTestStore, today: Callable[[], date]) -> None:
        self._tests = tests
        self._today = today

    def __call__(self) -> RetestStatus:
        """The status of the retest window against `today` (the profile's local date).

        Raises:
            AssessmentStoreError: unreadable data.
        """
        return retest_status((t.test_day for t in self._tests.list_tests()), self._today())
