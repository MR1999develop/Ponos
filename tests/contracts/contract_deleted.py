# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of the deleted activities (tombstones) and of `ActivityIndex.remove`.

Tried through a profile created by the `ProfileStorage` of the `harness` fixture, so the same
rules run on the in-memory fake and on SQLite.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import fields
from datetime import timedelta

import pytest

from ponos_coach.activities.domain import ActivityOrigin, GarminActivityId
from ponos_coach.activities.ports import (
    ActivityIndexError,
    ActivityNotFoundError,
    DuplicateActivityError,
)
from tests.builders import DELETED_AT, T0, fit_identity, make_deleted, make_record
from tests.contracts.contract_storage import FINGERPRINT, LOAD, create
from tests.contracts.harness import StorageHarness


class DeletedActivitiesContract:
    """An identity is in the index or among the deleted, never in both."""

    def test_a_tombstone_is_found_by_identity_and_comes_back_equal(
        self, harness: StorageHarness
    ) -> None:
        tombstone = make_deleted(make_record(fit_identity(1)))
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(tombstone)

            assert profile.deleted_activities.get(fit_identity(1)) == tombstone
            assert profile.deleted_activities.get(fit_identity(2)) is None

    def test_a_tombstone_keeps_origin_and_garmin_id_and_is_found_by_the_id(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(origin=ActivityOrigin.GARMIN, garmin_activity_id=77)
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(record))

            found = profile.deleted_activities.find_by_garmin_id(GarminActivityId(77))
            assert found is not None
            assert found.origin is ActivityOrigin.GARMIN
            assert found.garmin_activity_id == 77
            assert profile.deleted_activities.find_by_garmin_id(GarminActivityId(78)) is None

    def test_the_same_identity_twice_is_a_duplicate(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(make_record(fit_identity(1))))

            with pytest.raises(DuplicateActivityError):
                profile.deleted_activities.add(make_deleted(make_record(fit_identity(1))))

    def test_the_same_garmin_id_twice_is_a_duplicate(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            first = make_record(fit_identity(1), garmin_activity_id=5)
            second = make_record(fit_identity(2), garmin_activity_id=5)
            profile.deleted_activities.add(make_deleted(first))

            with pytest.raises(DuplicateActivityError):
                profile.deleted_activities.add(make_deleted(second))

    def test_remove_forgets_the_tombstone_and_a_missing_one_is_not_found(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(make_record(fit_identity(1))))

            profile.deleted_activities.remove(fit_identity(1))

            assert profile.deleted_activities.get(fit_identity(1)) is None
            with pytest.raises(ActivityNotFoundError):
                profile.deleted_activities.remove(fit_identity(1))

    def test_the_list_has_the_latest_deletion_first(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            for number, minutes in ((1, 0), (2, 20), (3, 10)):
                record = make_record(fit_identity(number))
                profile.deleted_activities.add(
                    make_deleted(record, at=DELETED_AT + timedelta(minutes=minutes))
                )

            listed = profile.deleted_activities.list_latest_first()

            assert [d.identity for d in listed] == [fit_identity(n) for n in (2, 3, 1)]

    def test_possible_duplicates_are_those_starting_close_with_a_similar_duration(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(make_record(fit_identity(1), start=T0)))
            far = make_record(fit_identity(2), start=T0 + timedelta(hours=3))
            profile.deleted_activities.add(make_deleted(far))
            short = make_record(fit_identity(3), start=T0, elapsed_s=600.0)
            profile.deleted_activities.add(make_deleted(short))

            found = profile.deleted_activities.find_possible_duplicates(
                T0 + timedelta(seconds=30), 3600.0
            )

            assert [d.identity for d in found] == [fit_identity(1)]

    def test_a_tombstone_does_not_keep_position_or_heart_rate(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(make_record()))

            tombstone = profile.deleted_activities.get(fit_identity())

        assert tombstone is not None
        assert {f.name for f in fields(tombstone)} == {
            "identity",
            "file_format",
            "sport",
            "start",
            "elapsed_s",
            "distance_m",
            "archived",
            "origin",
            "imported_at",
            "garmin_activity_id",
            "deleted_at",
        }


class ActivityIndexRemovalContract:
    """Removing a record, and the rule that a deleted identity cannot be indexed."""

    def test_remove_takes_the_record_out(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1)))
            profile.activities.add(make_record(fit_identity(2)))

            profile.activities.remove(fit_identity(1))

            assert [r.identity for r in profile.activities.list_by_start()] == [fit_identity(2)]
            assert profile.activities.get(fit_identity(1)) is None

    def test_remove_drops_the_cached_load_and_frees_the_garmin_id(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(origin=ActivityOrigin.GARMIN, garmin_activity_id=9)
        with closing(create(harness)) as profile:
            profile.activities.add(record)
            profile.load_cache.put(record.identity, FINGERPRINT, LOAD)

            profile.activities.remove(record.identity)

            assert profile.load_cache.get(record.identity, FINGERPRINT) is None
            assert profile.activities.find_by_garmin_id(GarminActivityId(9)) is None

    def test_remove_of_a_missing_record_is_not_found(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(ActivityNotFoundError):
            profile.activities.remove(fit_identity(1))

    def test_a_deleted_identity_cannot_be_added_to_the_index(self, harness: StorageHarness) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(record))

            with pytest.raises(ActivityIndexError):
                profile.activities.add(record)

            assert profile.activities.get(fit_identity(1)) is None

    def test_an_identity_still_in_the_index_cannot_be_marked_deleted(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            with pytest.raises(ActivityIndexError):
                profile.deleted_activities.add(make_deleted(record))

            assert profile.deleted_activities.get(fit_identity(1)) is None

    def test_deleting_is_all_or_nothing_inside_a_transaction(self, harness: StorageHarness) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.activities.remove(record.identity)
                profile.deleted_activities.add(make_deleted(record))
                raise RuntimeError("interrupted")

            assert profile.activities.get(record.identity) == record
            assert profile.deleted_activities.get(record.identity) is None

    def test_restoring_is_one_transaction_too(self, harness: StorageHarness) -> None:
        record = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.deleted_activities.add(make_deleted(record))

            with profile.activities.transaction():
                profile.deleted_activities.remove(record.identity)
                profile.activities.add(record)

            assert profile.activities.get(record.identity) == record
            assert profile.deleted_activities.get(record.identity) is None
