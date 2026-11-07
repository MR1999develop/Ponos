# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of `ProfileStorage` and of the four stores on the same connection.

`ActivityIndex`, `SyncJournal`, `PhysiologyStore` and `LoadCache` are tried through a profile
created by the `ProfileStorage` of the `harness` fixture.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, date, datetime, timedelta

import pytest

from ponos_coach.activities.domain import (
    ActivityOrigin,
    ActivitySummary,
    GarminActivityId,
    GeoPoint,
    Sport,
    encode_polyline,
)
from ponos_coach.activities.ports import DuplicateActivityError, TransactionStateError
from ponos_coach.load.domain import (
    ActivityLoad,
    LoadFingerprint,
    LoadSource,
    PhysiologyEntry,
    SportWeight,
    ZoneSeconds,
    default_weight,
    load_fingerprint,
)
from ponos_coach.load.ports import UnknownActivityError
from ponos_coach.profiles.domain import (
    BirthDate,
    FixedProfileData,
    Height,
    ProfileId,
    ProfileName,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import (
    FixedDataAlreadySetError,
    OpenProfile,
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)
from tests.builders import (
    IMPORTED_AT,
    T0,
    fit_identity,
    gpx_identity,
    make_record,
    physiology_entry,
    remote_activity,
)
from tests.contracts.harness import StorageHarness

PROFILE_ID = ProfileId.parse("11111111-1111-4111-8111-111111111111")
NAME = ProfileName.parse("Atleta di prova")
FINGERPRINT = LoadFingerprint("a" * 64)
OTHER_FINGERPRINT = LoadFingerprint("b" * 64)
LOAD = ActivityLoad(80.0, 75.0, 75.0, 3500, 4, 12.5, LoadSource.HEART_RATE)


def create(harness: StorageHarness) -> OpenProfile:
    """A new profile in a new folder."""
    return harness.storage.create(harness.new_folder(), PROFILE_ID, NAME, "Europe/Rome")


class ProfileStorageContract:
    """Create and open the database of a profile."""

    def test_create_reports_the_profile_info(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.info.profile_id == PROFILE_ID
            assert profile.info.name == NAME
            assert profile.info.timezone_name == "Europe/Rome"
            assert profile.info.format_version >= 1

    def test_open_finds_what_create_wrote(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome").close()

        with closing(harness.storage.open(folder)) as profile:
            assert profile.info.profile_id == PROFILE_ID
            assert profile.info.name == NAME

    def test_create_never_overwrites_an_existing_profile(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome").close()

        with pytest.raises(ProfileAlreadyExistsError):
            harness.storage.create(folder, PROFILE_ID, ProfileName.parse("Atleta B"), "UTC")
        with closing(harness.storage.open(folder)) as profile:
            assert profile.info.name == NAME

    def test_open_in_a_folder_without_database_fails(self, harness: StorageHarness) -> None:
        with pytest.raises(ProfileNotFoundError):
            harness.storage.open(harness.new_folder())

    def test_peek_reports_info_and_activity_count_without_opening(
        self, harness: StorageHarness
    ) -> None:
        folder = harness.new_folder()
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")) as profile:
            profile.activities.add(make_record())
            profile.activities.add(make_record(gpx_identity()))

        peeked = harness.storage.peek(folder)

        assert peeked.info.profile_id == PROFILE_ID
        assert peeked.info.name == NAME
        assert peeked.info.timezone_name == "Europe/Rome"
        assert peeked.activity_count == 2

    def test_peek_of_a_new_profile_counts_zero_activities(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        harness.storage.create(folder, PROFILE_ID, NAME, "UTC").close()

        assert harness.storage.peek(folder).activity_count == 0

    def test_peek_works_while_the_profile_is_open(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "UTC")) as profile:
            profile.activities.add(make_record())

            assert harness.storage.peek(folder).activity_count == 1

    def test_peek_in_a_folder_without_database_fails(self, harness: StorageHarness) -> None:
        with pytest.raises(ProfileNotFoundError):
            harness.storage.peek(harness.new_folder())

    def test_unknown_timezone_is_refused(self, harness: StorageHarness) -> None:
        with pytest.raises(ValueError, match="time zone"):
            harness.storage.create(harness.new_folder(), PROFILE_ID, NAME, "Mars/Olympus")

    def test_close_can_be_repeated(self, harness: StorageHarness) -> None:
        profile = create(harness)

        profile.close()
        profile.close()

    def test_one_transaction_covers_all_four_stores(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            record = make_record()
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.activities.add(record)
                profile.sync_journal.add_pending([remote_activity(1)], IMPORTED_AT)
                profile.physiology.put(physiology_entry(date(2026, 1, 1)))
                profile.load_cache.put(record.identity, FINGERPRINT, LOAD)
                raise RuntimeError("boom")

            assert profile.activities.list_by_start() == []
            assert profile.sync_journal.get(GarminActivityId(1001)) is None
            assert profile.physiology.list_entries() == []
            assert profile.load_cache.get(record.identity, FINGERPRINT) is None


class ActivityIndexContract:
    """The activity index of a profile."""

    def test_new_index_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            index = profile.activities
            assert index.list_by_start() == []
            assert not index.exists(fit_identity())
            assert index.get(fit_identity()) is None

    def test_added_record_is_found_and_returned_unchanged(self, harness: StorageHarness) -> None:
        summary = ActivitySummary(
            12_345.6,
            210.0,
            GeoPoint(-30.0, -20.0),
            encode_polyline([GeoPoint(-30.0, -20.0), GeoPoint(-30.01, -20.01)]),
        )
        record = make_record(fit_identity(), summary=summary, sport=Sport.TRAIL)
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            assert profile.activities.exists(record.identity)
            assert profile.activities.get(record.identity) == record
            assert profile.activities.list_by_start() == [record]

    def test_garmin_origin_and_id_are_kept(self, harness: StorageHarness) -> None:
        record = make_record(origin=ActivityOrigin.GARMIN, garmin_activity_id=987_654_321_012)
        with closing(create(harness)) as profile:
            profile.activities.add(record)

            assert profile.activities.get(record.identity) == record

    def test_records_are_listed_by_start_then_identity(self, harness: StorageHarness) -> None:
        later = make_record(fit_identity(1), start=T0 + timedelta(days=1))
        earlier = make_record(fit_identity(2), start=T0)
        same_start_gpx = make_record(gpx_identity(1), start=T0)
        with closing(create(harness)) as profile:
            for record in (later, same_start_gpx, earlier):
                profile.activities.add(record)

            listed = profile.activities.list_by_start()

        # On equal start the order of the identity keys applies: "fit:" comes before "gpx:".
        assert listed == [earlier, same_start_gpx, later]

    def test_fit_and_gpx_identities_are_different_activities(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1)))
            profile.activities.add(make_record(gpx_identity(1)))

            assert len(profile.activities.list_by_start()) == 2

    def test_duplicate_identity_is_refused_and_changes_nothing(
        self, harness: StorageHarness
    ) -> None:
        original = make_record(fit_identity(), elapsed_s=3600.0)
        with closing(create(harness)) as profile:
            profile.activities.add(original)

            with pytest.raises(DuplicateActivityError):
                profile.activities.add(make_record(fit_identity(), elapsed_s=1800.0))

            assert profile.activities.list_by_start() == [original]

    def test_duplicate_garmin_id_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1), garmin_activity_id=42))

            with pytest.raises(DuplicateActivityError):
                profile.activities.add(make_record(fit_identity(2), garmin_activity_id=42))

            assert len(profile.activities.list_by_start()) == 1

    def test_committed_transaction_keeps_its_changes(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with profile.activities.transaction():
                profile.activities.add(make_record(fit_identity(1)))
                profile.activities.add(make_record(fit_identity(2)))

            assert len(profile.activities.list_by_start()) == 2

    def test_failed_transaction_leaves_no_change_and_reraises(
        self, harness: StorageHarness
    ) -> None:
        kept = make_record(fit_identity(1))
        with closing(create(harness)) as profile:
            profile.activities.add(kept)

            with pytest.raises(DuplicateActivityError), profile.activities.transaction():
                profile.activities.add(make_record(fit_identity(2)))
                profile.activities.add(kept)

            assert profile.activities.list_by_start() == [kept]

    def test_nested_transaction_is_refused_and_outer_one_still_commits(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            with profile.activities.transaction():
                with pytest.raises(TransactionStateError), profile.activities.transaction():
                    pass
                profile.activities.add(make_record(fit_identity(1)))

            assert profile.activities.exists(fit_identity(1))

    def test_records_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        record = make_record(fit_identity(), summary=ActivitySummary(distance_m=5_000.0))
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.activities.add(record)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.activities.list_by_start() == [record]

    def test_possible_duplicates_are_found_by_start_and_duration(
        self, harness: StorageHarness
    ) -> None:
        record = make_record(gpx_identity(), start=T0, elapsed_s=1000.0)
        with closing(create(harness)) as profile:
            profile.activities.add(record)
            find = profile.activities.find_possible_duplicates

            assert find(T0, 1000.0) == [record]
            assert find(T0 + timedelta(seconds=60), 1000.0) == [record]  # on point
            assert find(T0 - timedelta(seconds=60), 1000.0) == [record]
            assert find(T0 + timedelta(seconds=61), 1000.0) == []  # off point
            assert find(T0, 1050.0) == [record]  # 5% of the larger: on point
            assert find(T0, 1053.0) == []  # off point
            assert find(T0, 950.0) == [record]  # on point: 50 s is 5% of 1000
            assert find(T0, 949.0) == []  # off point

    def test_no_possible_duplicates_in_an_empty_index(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.activities.find_possible_duplicates(T0, 1000.0) == []

    def test_start_far_from_record_is_not_a_duplicate_even_with_same_duration(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(start=T0, elapsed_s=1000.0))

            assert (
                profile.activities.find_possible_duplicates(
                    datetime(2026, 3, 3, 7, 0, tzinfo=UTC), 1000.0
                )
                == []
            )


class PhysiologyStoreContract:
    """Physiological parameters with validity date."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.physiology.list_entries() == []

    def test_entries_are_listed_by_date(self, harness: StorageHarness) -> None:
        march = physiology_entry(date(2026, 3, 1), 170)
        january = physiology_entry(date(2026, 1, 1), 165)
        with closing(create(harness)) as profile:
            profile.physiology.put(march)
            profile.physiology.put(january)

            assert profile.physiology.list_entries() == [january, march]

    def test_same_date_replaces_the_entry(self, harness: StorageHarness) -> None:
        first = physiology_entry(date(2026, 1, 1), 165)
        corrected = physiology_entry(date(2026, 1, 1), 168)
        with closing(create(harness)) as profile:
            profile.physiology.put(first)
            profile.physiology.put(corrected)

            assert profile.physiology.list_entries() == [corrected]

    def test_entries_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        entry: PhysiologyEntry = physiology_entry(date(2026, 1, 1))
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.physiology.put(entry)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.physiology.list_entries() == [entry]


class LoadCacheContract:
    """Loads tied to the fingerprint of the parameters that produced them."""

    def test_miss_when_nothing_was_stored(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())

            assert profile.load_cache.get(fit_identity(), FINGERPRINT) is None

    def test_hit_returns_the_stored_load(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)

            assert profile.load_cache.get(fit_identity(), FINGERPRINT) == LOAD

    def test_load_produced_with_other_parameters_is_a_miss(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)

            assert profile.load_cache.get(fit_identity(), OTHER_FINGERPRINT) is None

    def test_put_replaces_the_previous_load(self, harness: StorageHarness) -> None:
        recomputed = ActivityLoad(10.0, 9.0, 9.0, 100, 0, 0.0, LoadSource.ESTIMATE)
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)
            profile.load_cache.put(fit_identity(), OTHER_FINGERPRINT, recomputed)

            assert profile.load_cache.get(fit_identity(), FINGERPRINT) is None
            assert profile.load_cache.get(fit_identity(), OTHER_FINGERPRINT) == recomputed

    def test_invalidate_forgets_one_activity_only(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record(fit_identity(1)))
            profile.activities.add(make_record(fit_identity(2)))
            profile.load_cache.put(fit_identity(1), FINGERPRINT, LOAD)
            profile.load_cache.put(fit_identity(2), FINGERPRINT, LOAD)

            profile.load_cache.invalidate(fit_identity(1))
            profile.load_cache.invalidate(fit_identity(3))  # unknown: no effect

            assert profile.load_cache.get(fit_identity(1), FINGERPRINT) is None
            assert profile.load_cache.get(fit_identity(2), FINGERPRINT) == LOAD

    def test_clear_forgets_everything(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)

            profile.load_cache.clear()

            assert profile.load_cache.get(fit_identity(), FINGERPRINT) is None

    def test_load_for_an_unknown_activity_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(UnknownActivityError):
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)

    def test_loads_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        fingerprint = load_fingerprint(physiology_entry(date(2026, 1, 1)).physiology, {})
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.activities.add(make_record())
        profile.load_cache.put(fit_identity(), fingerprint, LOAD)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.load_cache.get(fit_identity(), fingerprint) == LOAD


class LoadCacheZonesContract:
    """Seconds in zone travel with the load and with its fingerprint."""

    def test_zone_seconds_are_stored_with_the_load(self, harness: StorageHarness) -> None:
        zoned = ActivityLoad(
            80.0, 75.0, 75.0, 3500, 4, 12.5, LoadSource.HEART_RATE,
            ZoneSeconds((10.0, 20.5, 30.0, 40.25, 50.0)),
        )  # fmt: skip
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, zoned)

            found = profile.load_cache.get(fit_identity(), FINGERPRINT)

            assert found == zoned
            assert found is not None
            assert found.zone_seconds.values == (10.0, 20.5, 30.0, 40.25, 50.0)

    def test_a_load_without_zones_comes_back_with_zero_seconds(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, LOAD)

            found = profile.load_cache.get(fit_identity(), FINGERPRINT)

            assert found is not None
            assert found.zone_seconds == ZoneSeconds.ZERO

    def test_replacing_a_load_replaces_its_zones_too(self, harness: StorageHarness) -> None:
        first = ActivityLoad(
            1.0, 1.0, 1.0, 1, 0, 0.0, LoadSource.HEART_RATE, ZoneSeconds((1.0, 2.0, 3.0, 4.0, 5.0))
        )
        second = ActivityLoad(
            1.0, 1.0, 1.0, 1, 0, 0.0, LoadSource.HEART_RATE, ZoneSeconds((5.0, 0.0, 0.0, 0.0, 1.0))
        )
        with closing(create(harness)) as profile:
            profile.activities.add(make_record())
            profile.load_cache.put(fit_identity(), FINGERPRINT, first)
            profile.load_cache.put(fit_identity(), FINGERPRINT, second)

            assert profile.load_cache.get(fit_identity(), FINGERPRINT) == second


class WeightStoreContract:
    """Weight series with validity date."""

    def test_new_store_is_empty(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.weights.list_entries() == []

    def test_entries_are_listed_by_date(self, harness: StorageHarness) -> None:
        march = WeightEntry(date(2026, 3, 1), Weight(705))
        january = WeightEntry(date(2026, 1, 1), Weight(712))
        with closing(create(harness)) as profile:
            profile.weights.put(march)
            profile.weights.put(january)

            assert profile.weights.list_entries() == [january, march]

    def test_same_date_replaces_the_entry(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.weights.put(WeightEntry(date(2026, 1, 1), Weight(700)))
            profile.weights.put(WeightEntry(date(2026, 1, 1), Weight(698)))

            assert profile.weights.list_entries() == [WeightEntry(date(2026, 1, 1), Weight(698))]

    def test_the_limits_of_the_weight_range_are_kept_exactly(self, harness: StorageHarness) -> None:
        lowest = WeightEntry(date(2026, 1, 1), Weight(200))
        highest = WeightEntry(date(2026, 1, 2), Weight(3000))
        with closing(create(harness)) as profile:
            profile.weights.put(lowest)
            profile.weights.put(highest)

            assert profile.weights.list_entries() == [lowest, highest]

    def test_entries_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        entry = WeightEntry(date(2026, 1, 1), Weight(705))
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.weights.put(entry)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.weights.list_entries() == [entry]

    def test_a_cancelled_transaction_leaves_no_weight(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.weights.put(WeightEntry(date(2026, 1, 1), Weight(705)))
                raise RuntimeError("boom")

            assert profile.weights.list_entries() == []


class FixedDataStoreContract:
    """Height and date of birth: set only once."""

    DATA = FixedProfileData(Height(172), BirthDate(date(1990, 5, 17)))

    def test_new_profile_has_no_fixed_data(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.fixed_data.get() is None

    def test_set_once_then_get(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.fixed_data.set_once(self.DATA)

            assert profile.fixed_data.get() == self.DATA

    def test_a_second_write_is_refused_and_changes_nothing(self, harness: StorageHarness) -> None:
        other = FixedProfileData(Height(180), BirthDate(date(1985, 1, 1)))
        with closing(create(harness)) as profile:
            profile.fixed_data.set_once(self.DATA)

            with pytest.raises(FixedDataAlreadySetError):
                profile.fixed_data.set_once(other)

            assert profile.fixed_data.get() == self.DATA

    def test_the_limits_are_kept_exactly(self, harness: StorageHarness) -> None:
        extreme = FixedProfileData(Height(250), BirthDate(date(1900, 1, 1)))
        with closing(create(harness)) as profile:
            profile.fixed_data.set_once(extreme)

            assert profile.fixed_data.get() == extreme

    def test_data_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.fixed_data.set_once(self.DATA)
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.fixed_data.get() == self.DATA

    def test_a_cancelled_transaction_leaves_no_fixed_data(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.fixed_data.set_once(self.DATA)
                raise RuntimeError("boom")

            assert profile.fixed_data.get() is None
            profile.fixed_data.set_once(self.DATA)  # and it can still be set


class SportWeightStoreContract:
    """Sport weights chosen by the user."""

    def test_new_profile_has_no_saved_weights(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.sport_weights.load() == {}

    def test_replace_then_load(self, harness: StorageHarness) -> None:
        weights = {Sport.RUNNING: SportWeight(1.25), Sport.CYCLING: SportWeight(0.0)}
        with closing(create(harness)) as profile:
            profile.sport_weights.replace(weights)

            assert dict(profile.sport_weights.load()) == weights

    def test_replace_drops_the_sports_not_given(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.sport_weights.replace({Sport.RUNNING: SportWeight(1.25)})
            profile.sport_weights.replace({Sport.HIKING: SportWeight(0.5)})

            assert dict(profile.sport_weights.load()) == {Sport.HIKING: SportWeight(0.5)}

    def test_replace_with_nothing_clears_the_saved_weights(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.sport_weights.replace({Sport.RUNNING: SportWeight(1.25)})
            profile.sport_weights.replace({})

            assert profile.sport_weights.load() == {}

    def test_weights_are_stored_exactly(self, harness: StorageHarness) -> None:
        exact = {Sport.TRAIL: default_weight(Sport.TRAIL), Sport.OTHER: SportWeight(5.0)}
        with closing(create(harness)) as profile:
            profile.sport_weights.replace(exact)

            assert dict(profile.sport_weights.load()) == exact

    def test_weights_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        profile = harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")
        profile.sport_weights.replace({Sport.STRENGTH: SportWeight(1.3)})
        profile.close()

        with closing(harness.storage.open(folder)) as reopened:
            assert dict(reopened.sport_weights.load()) == {Sport.STRENGTH: SportWeight(1.3)}

    def test_a_cancelled_transaction_keeps_the_previous_weights(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.sport_weights.replace({Sport.RUNNING: SportWeight(1.25)})
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.sport_weights.replace({Sport.CYCLING: SportWeight(2.0)})
                raise RuntimeError("boom")

            assert dict(profile.sport_weights.load()) == {Sport.RUNNING: SportWeight(1.25)}
