# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contratti di `ProfileStorage` e dei tre store sulla stessa connessione.

`ActivityIndex`, `PhysiologyStore` e `LoadCache` si provano attraverso un profilo creato dal
`ProfileStorage` della fixture `harness`.
"""

from __future__ import annotations

from contextlib import closing
from datetime import UTC, date, datetime, timedelta

import pytest

from ponos_coach.activities.domain import (
    ActivityOrigin,
    ActivitySummary,
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
    load_fingerprint,
)
from ponos_coach.load.ports import UnknownActivityError
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import (
    OpenProfile,
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)
from tests.builders import T0, fit_identity, gpx_identity, make_record, physiology_entry
from tests.contracts.harness import StorageHarness

PROFILE_ID = ProfileId.parse("11111111-1111-4111-8111-111111111111")
NAME = ProfileName.parse("Marta")
FINGERPRINT = LoadFingerprint("a" * 64)
OTHER_FINGERPRINT = LoadFingerprint("b" * 64)
LOAD = ActivityLoad(80.0, 75.0, 75.0, 3500, 4, 12.5, LoadSource.HEART_RATE)


def create(harness: StorageHarness) -> OpenProfile:
    """Un profilo nuovo in una cartella nuova."""
    return harness.storage.create(harness.new_folder(), PROFILE_ID, NAME, "Europe/Rome")


class ProfileStorageContract:
    """Creare e aprire il database di un profilo."""

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
            harness.storage.create(folder, PROFILE_ID, ProfileName.parse("Altro"), "UTC")
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
        with pytest.raises(ValueError, match="fuso"):
            harness.storage.create(harness.new_folder(), PROFILE_ID, NAME, "Mars/Olympus")

    def test_close_can_be_repeated(self, harness: StorageHarness) -> None:
        profile = create(harness)

        profile.close()
        profile.close()

    def test_one_transaction_covers_all_three_stores(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            record = make_record()
            with pytest.raises(RuntimeError, match="boom"), profile.activities.transaction():
                profile.activities.add(record)
                profile.physiology.put(physiology_entry(date(2026, 1, 1)))
                profile.load_cache.put(record.identity, FINGERPRINT, LOAD)
                raise RuntimeError("boom")

            assert profile.activities.list_by_start() == []
            assert profile.physiology.list_entries() == []
            assert profile.load_cache.get(record.identity, FINGERPRINT) is None


class ActivityIndexContract:
    """L'indice delle attività di un profilo."""

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

        # A parità di inizio vale l'ordine delle chiavi d'identità: "fit:" precede "gpx:".
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
            assert find(T0, 1050.0) == [record]  # 5% del maggiore: on point
            assert find(T0, 1053.0) == []  # off point
            assert find(T0, 950.0) == [record]  # on point: 50 s sono il 5% di 1000
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
    """Parametri fisiologici con data di validità."""

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
    """Carichi legati all'impronta dei parametri che li hanno prodotti."""

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
            profile.load_cache.invalidate(fit_identity(3))  # sconosciuta: nessun effetto

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
