# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 4 test process: applies or removes a threshold test and dies in the middle of it.

Usage: `python -I collaudo_fase4_worker.py <profile-folder> <mode>`, with `mode` one of:

- `apply-before-history`: dies after the threshold measurement is written, before the test is
  added to the history (transaction open);
- `apply-after-history`: dies after the history insert, before the commit;
- `apply-complete`: applies and exits normally (control run);
- `remove-after-history`: dies after the test is removed from the history, before the measurement
  is taken back;
- `remove-after-measurement`: dies after the measurement is removed, before the commit.

The profile must already hold one activity (the first of the index is the test) and, for `remove-*`,
the test applied on a day that had no entry. `os._exit` skips every cleanup, like a power-off.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.assessment.domain import SegmentSelection, ThresholdTest, ThresholdTestResult
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.profiles.measurements import UpdateMeasurements, ValidityPeriod
from ponos_coach.profiles.threshold_tests import (
    AppliedThresholdTest,
    ApplyThresholdTest,
    RemoveThresholdTest,
)
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage

DIED = 77
TEST_DAY = date(2026, 3, 14)
TODAY = date(2026, 7, 1)


class DyingTests:
    """The history store, dying right after or before one operation."""

    def __init__(self, inner: ThresholdTestStore, die_before_add: bool, die_after: str) -> None:
        self._inner = inner
        self._die_before_add = die_before_add
        self._die_after = die_after

    def add(self, test: ThresholdTest) -> None:
        if self._die_before_add:
            os._exit(DIED)
        self._inner.add(test)
        if self._die_after == "add":
            os._exit(DIED)

    def remove(self, identity: ActivityIdentity) -> None:
        self._inner.remove(identity)
        if self._die_after == "remove":
            os._exit(DIED)

    def get(self, identity: ActivityIdentity) -> ThresholdTest | None:
        return self._inner.get(identity)

    def list_tests(self) -> Sequence[ThresholdTest]:
        return self._inner.list_tests()


class DyingPhysiology:
    """The physiology store, dying after removing an entry."""

    def __init__(self, inner: PhysiologyStore, die_after_remove: bool) -> None:
        self._inner = inner
        self._die_after_remove = die_after_remove

    def put(self, entry: PhysiologyEntry) -> None:
        self._inner.put(entry)

    def remove(self, valid_from: date) -> None:
        self._inner.remove(valid_from)
        if self._die_after_remove:
            os._exit(DIED)

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        return self._inner.list_entries()


def main(folder: Path, mode: str) -> int:
    opened = SqliteProfileStorage().open(folder)
    try:
        [record] = opened.activities.list_by_start()
        tests = DyingTests(
            opened.threshold_tests,
            die_before_add=mode == "apply-before-history",
            die_after={"apply-after-history": "add", "remove-after-history": "remove"}.get(
                mode, ""
            ),
        )
        physiology = DyingPhysiology(opened.physiology, mode == "remove-after-measurement")
        measurements = UpdateMeasurements(
            opened.activities,
            opened.physiology,
            opened.weights,
            lambda: TODAY,
            opened.fixed_data,
            opened.threshold_paces,
        )
        if mode.startswith("apply"):
            result = ThresholdTestResult(
                identity=record.identity,
                test_day=TEST_DAY,
                selection=SegmentSelection.BY_DISTANCE,
                segment_start_s=600.0,
                segment_start=datetime(2026, 3, 14, 7, 10, tzinfo=UTC),
                threshold_bpm=165,
                pace_s_per_km=286,
                valid_hr_samples=1200,
                hr_zone_starts_bpm=(140.25, 148.5, 156.75, 165.0),
                pace_zone_starts_s_per_km=(366.0, 325.0, 304.0, 286.0),
            )
            ApplyThresholdTest(opened.activities, tests, physiology, measurements)(result)
        else:
            [test] = opened.threshold_tests.list_tests()
            written = next(e for e in opened.physiology.list_entries() if e.valid_from == TEST_DAY)
            applied = AppliedThresholdTest(test, ValidityPeriod(TEST_DAY, None), written, None)
            RemoveThresholdTest(opened.activities, tests, physiology)(applied)
    finally:
        opened.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), sys.argv[2]))
