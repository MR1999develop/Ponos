# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A profile in memory with an archive and the threshold test use cases wired to it."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from ponos_coach.activities.domain import ActivityOrigin, ActivityRecord, FileFormat
from ponos_coach.activities.fit_decoder import decode_activity
from ponos_coach.assessment.use_cases import EvaluateThresholdTest, ListThresholdTests, NextRetest
from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.profiles.measurements import UpdateMeasurements
from ponos_coach.profiles.threshold_tests import ApplyThresholdTest, RemoveThresholdTest
from tests.builders import IMPORTED_AT, physiology_entry
from tests.load.test_profile_overview import ROME, World, decode_fit

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data" / "fit"
TODAY = date(2026, 3, 20)
FIRST_DAY = date(2026, 1, 1)
TEST_DAY = date(2026, 3, 14)  # the day of the threshold test fixtures (07:00 UTC, 08:00 in Rome)


class ThresholdWorld(World):
    """A `World` with physiological parameters from 1 January (50-190 bpm, threshold 170) unless
    told otherwise."""

    def __init__(self, *, with_physiology: bool = True) -> None:
        super().__init__()
        if with_physiology:
            self.profile.physiology.put(physiology_entry(FIRST_DAY, 170))
        self.evaluate = EvaluateThresholdTest(
            self.profile.activities,
            self.archive,
            self.profile.physiology,
            decode_fit,
            timezone=ROME,
        )
        self.measurements = UpdateMeasurements(
            self.profile.activities,
            self.profile.physiology,
            self.profile.weights,
            lambda: TODAY,
            self.profile.fixed_data,
            self.profile.threshold_paces,
        )
        self.apply = ApplyThresholdTest(
            self.profile.activities,
            self.profile.threshold_tests,
            self.profile.physiology,
            self.measurements,
        )
        self.remove = RemoveThresholdTest(
            self.profile.activities, self.profile.threshold_tests, self.profile.physiology
        )
        self.list_tests = ListThresholdTests(self.profile.threshold_tests)
        self.next_retest = NextRetest(self.profile.threshold_tests, lambda: TODAY)

    def add_fixture(self, name: str) -> ActivityRecord:
        """Imports a FIT fixture into the profile and its archive."""
        data = (FIXTURES / name).read_bytes()
        activity = decode_activity(data)
        archived = self.archive.store(data, FileFormat.FIT, activity.start)
        record = ActivityRecord.from_activity(
            activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT
        )
        self.profile.activities.add(record)
        return record

    def entries(self) -> list[PhysiologyEntry]:
        return list(self.profile.physiology.list_entries())
