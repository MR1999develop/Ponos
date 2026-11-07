# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of the threshold test history and of the removal of a physiological entry.

They are tried through a profile created by the `ProfileStorage` of the `harness` fixture, on the
in-memory fakes and on SQLite alike.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, date, datetime

import pytest

from ponos_coach.assessment.domain import ThresholdTest
from ponos_coach.assessment.ports import ThresholdTestAlreadyStoredError, ThresholdTestNotFoundError
from ponos_coach.load.ports import PhysiologyEntryNotFoundError
from tests.builders import fit_identity, gpx_identity, physiology_entry
from tests.contracts.contract_storage import NAME, PROFILE_ID, create
from tests.contracts.harness import StorageHarness

START = datetime(2026, 3, 2, 7, 10, tzinfo=UTC)


def a_test(
    number: int = 1,
    day: date = date(2026, 3, 2),
    start: datetime = START,
    bpm: int = 168,
    pace: int | None = 300,
) -> ThresholdTest:
    """A history entry for the FIT activity `number`."""
    return ThresholdTest(fit_identity(number), day, start, bpm, pace)


class ThresholdTestStoreContract:
    """History of the applied tests: one per activity, listed by day."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.threshold_tests.list_tests() == []

    def test_a_test_is_kept_whole_and_found_by_its_activity(self, harness: StorageHarness) -> None:
        entry = a_test()
        with closing(create(harness)) as profile:
            profile.threshold_tests.add(entry)

            assert profile.threshold_tests.get(entry.identity) == entry
            assert profile.threshold_tests.list_tests() == [entry]

    def test_a_test_without_pace_comes_back_without_it(self, harness: StorageHarness) -> None:
        entry = a_test(pace=None)
        with closing(create(harness)) as profile:
            profile.threshold_tests.add(entry)

            assert profile.threshold_tests.get(entry.identity) == entry

    def test_a_gpx_activity_can_be_a_test_too(self, harness: StorageHarness) -> None:
        entry = ThresholdTest(gpx_identity(), date(2026, 3, 2), START, 160, 310)
        with closing(create(harness)) as profile:
            profile.threshold_tests.add(entry)

            assert profile.threshold_tests.list_tests() == [entry]

    def test_an_unknown_activity_has_no_test(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.threshold_tests.get(fit_identity(9)) is None

    def test_tests_are_listed_by_day_then_segment_start_then_activity(
        self, harness: StorageHarness
    ) -> None:
        later_start = datetime(2026, 3, 2, 18, 0, tzinfo=UTC)
        april = a_test(1, date(2026, 4, 20))
        march_evening = a_test(2, date(2026, 3, 2), later_start)
        march_morning = a_test(3, date(2026, 3, 2), START)
        with closing(create(harness)) as profile:
            for entry in (april, march_evening, march_morning):
                profile.threshold_tests.add(entry)

            assert profile.threshold_tests.list_tests() == [march_morning, march_evening, april]

    def test_the_same_activity_twice_is_refused_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        first = a_test(1, bpm=168)
        with closing(create(harness)) as profile:
            profile.threshold_tests.add(first)

            with pytest.raises(ThresholdTestAlreadyStoredError):
                profile.threshold_tests.add(a_test(1, bpm=171))

            assert profile.threshold_tests.list_tests() == [first]

    def test_removing_a_test_deletes_only_that_one(self, harness: StorageHarness) -> None:
        keep, drop = a_test(1), a_test(2, date(2026, 5, 1))
        with closing(create(harness)) as profile:
            profile.threshold_tests.add(keep)
            profile.threshold_tests.add(drop)

            profile.threshold_tests.remove(drop.identity)

            assert profile.threshold_tests.list_tests() == [keep]

    def test_removing_an_activity_that_is_not_a_test_is_refused(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile, pytest.raises(ThresholdTestNotFoundError):
            profile.threshold_tests.remove(fit_identity(5))

    def test_the_history_survives_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        entry = a_test()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.threshold_tests.add(entry)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.threshold_tests.list_tests() == [entry]

    def test_a_failed_transaction_leaves_no_test_behind(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.threshold_tests.add(a_test())
                raise RuntimeError("stop")

            assert profile.threshold_tests.list_tests() == []


class PhysiologyRemovalContract:
    """Removing the physiological entry that starts on a day."""

    def test_the_entry_of_that_day_is_removed_and_the_others_stay(
        self, harness: StorageHarness
    ) -> None:
        january = physiology_entry(date(2026, 1, 1), 165)
        march = physiology_entry(date(2026, 3, 1), 170)
        with closing(create(harness)) as profile:
            profile.physiology.put(january)
            profile.physiology.put(march)

            profile.physiology.remove(march.valid_from)

            assert profile.physiology.list_entries() == [january]

    def test_removing_a_day_without_an_entry_is_refused_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        january = physiology_entry(date(2026, 1, 1), 165)
        with closing(create(harness)) as profile:
            profile.physiology.put(january)

            with pytest.raises(PhysiologyEntryNotFoundError):
                profile.physiology.remove(date(2026, 1, 2))

            assert profile.physiology.list_entries() == [january]
