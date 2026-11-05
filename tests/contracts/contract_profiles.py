# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contratti di `ProfileRegistry` e `ProfileFolders`."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.profiles.domain import FolderState
from ponos_coach.profiles.ports import (
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    ProfileAlreadyRegisteredError,
    ProfileInUseError,
    ProfileNotRegisteredError,
)
from tests.builders import IMPORTED_AT, absolute_path, registry_entry
from tests.contracts.harness import FoldersHarness, RegistryHarness


class ProfileRegistryContract:
    """L'elenco dei profili e l'ultimo usato."""

    def test_new_registry_is_empty(self, harness: RegistryHarness) -> None:
        assert harness.registry.list_entries() == []

    def test_added_entry_is_listed_unchanged(self, harness: RegistryHarness) -> None:
        entry = registry_entry(1)

        harness.registry.add(entry)

        assert harness.registry.list_entries() == [entry]

    def test_entries_are_listed_from_the_most_recently_used(self, harness: RegistryHarness) -> None:
        old = registry_entry(1, last_used=IMPORTED_AT)
        recent = registry_entry(2, last_used=IMPORTED_AT + timedelta(days=1))
        harness.registry.add(old)
        harness.registry.add(recent)

        assert harness.registry.list_entries() == [recent, old]

    def test_same_id_or_same_folder_is_refused_and_changes_nothing(
        self, harness: RegistryHarness
    ) -> None:
        entry = registry_entry(1)
        harness.registry.add(entry)

        with pytest.raises(ProfileAlreadyRegisteredError):
            harness.registry.add(registry_entry(1, folder=absolute_path("elsewhere")))
        with pytest.raises(ProfileAlreadyRegisteredError):
            harness.registry.add(registry_entry(2, folder=entry.folder))

        assert harness.registry.list_entries() == [entry]

    def test_remove_drops_only_that_entry(self, harness: RegistryHarness) -> None:
        keep, drop = registry_entry(1), registry_entry(2)
        harness.registry.add(keep)
        harness.registry.add(drop)

        harness.registry.remove(drop.profile_id)

        assert harness.registry.list_entries() == [keep]

    def test_remove_of_an_unknown_profile_fails(self, harness: RegistryHarness) -> None:
        with pytest.raises(ProfileNotRegisteredError):
            harness.registry.remove(registry_entry(9).profile_id)

    def test_mark_used_moves_the_profile_first(self, harness: RegistryHarness) -> None:
        first, second = registry_entry(1), registry_entry(2, last_used=IMPORTED_AT - timedelta(1))
        harness.registry.add(first)
        harness.registry.add(second)
        later = IMPORTED_AT + timedelta(hours=1)

        harness.registry.mark_used(second.profile_id, later)
        listed = harness.registry.list_entries()

        assert [e.profile_id for e in listed] == [second.profile_id, first.profile_id]
        assert listed[0].last_used_utc == later

    def test_mark_used_of_an_unknown_profile_fails(self, harness: RegistryHarness) -> None:
        with pytest.raises(ProfileNotRegisteredError):
            harness.registry.mark_used(registry_entry(9).profile_id, IMPORTED_AT)

    def test_entries_survive_a_restart(self, harness: RegistryHarness) -> None:
        entry = registry_entry(1)
        harness.registry.add(entry)

        assert harness.reopen().list_entries() == [entry]


class ProfileFoldersContract:
    """Cartelle dei profili: ispezione, creazione, lock esclusivo, cancellazione."""

    def test_missing_folder_is_reported_missing_with_the_kind_of_its_place(
        self, harness: FoldersHarness
    ) -> None:
        inspection = harness.folders.inspect(harness.root / "nuova")

        assert inspection.state is FolderState.MISSING
        assert inspection.kind is harness.root_kind

    def test_empty_and_non_empty_folders_are_told_apart(self, harness: FoldersHarness) -> None:
        folder = harness.root / "dati"
        harness.folders.create_private_folder(folder)
        assert harness.folders.inspect(folder).state is FolderState.EMPTY

        harness.put_file(folder, "nota.txt")

        assert harness.folders.inspect(folder).state is FolderState.NOT_EMPTY

    def test_root_reports_its_kind(self, harness: FoldersHarness) -> None:
        assert harness.folders.inspect(harness.root).kind is harness.root_kind

    def test_create_makes_missing_parents_too(self, harness: FoldersHarness) -> None:
        folder = harness.root / "a" / "b" / "profilo"

        harness.folders.create_private_folder(folder)

        assert harness.folders.inspect(folder).state is FolderState.EMPTY

    def test_create_reuses_an_empty_folder(self, harness: FoldersHarness) -> None:
        harness.folders.create_private_folder(harness.root)

        assert harness.folders.inspect(harness.root).state is FolderState.EMPTY

    def test_create_refuses_a_non_empty_folder_and_changes_nothing(
        self, harness: FoldersHarness
    ) -> None:
        folder = harness.root / "pieno"
        harness.folders.create_private_folder(folder)
        harness.put_file(folder, "nota.txt")

        with pytest.raises(FolderNotEmptyError):
            harness.folders.create_private_folder(folder)

        assert harness.folders.inspect(folder).state is FolderState.NOT_EMPTY

    def test_lock_is_exclusive_until_released(self, harness: FoldersHarness) -> None:
        folder = harness.root / "profilo"
        harness.folders.create_private_folder(folder)
        lock = harness.folders.acquire_lock(folder)

        with pytest.raises(ProfileInUseError):
            harness.folders.acquire_lock(folder)

        lock.release()
        harness.folders.acquire_lock(folder).release()

    def test_releasing_twice_does_not_free_a_lock_taken_by_someone_else(
        self, harness: FoldersHarness
    ) -> None:
        folder = harness.root / "profilo"
        harness.folders.create_private_folder(folder)
        first = harness.folders.acquire_lock(folder)
        first.release()
        second = harness.folders.acquire_lock(folder)

        first.release()

        with pytest.raises(ProfileInUseError):
            harness.folders.acquire_lock(folder)
        second.release()

    def test_locks_of_different_folders_are_independent(self, harness: FoldersHarness) -> None:
        one, two = harness.root / "uno", harness.root / "due"
        harness.folders.create_private_folder(one)
        harness.folders.create_private_folder(two)

        first = harness.folders.acquire_lock(one)
        second = harness.folders.acquire_lock(two)

        first.release()
        second.release()

    def test_lock_of_a_missing_folder_fails(self, harness: FoldersHarness) -> None:
        with pytest.raises(FolderUnavailableError):
            harness.folders.acquire_lock(harness.root / "non-esiste")

    def test_delete_removes_a_profile_folder(self, harness: FoldersHarness) -> None:
        folder = harness.root / "profilo"
        harness.folders.create_private_folder(folder)
        harness.make_profile_folder(folder)
        harness.put_file(folder, "altro.dat")

        harness.folders.delete_folder(folder)

        assert harness.folders.inspect(folder).state is FolderState.MISSING

    def test_delete_refuses_a_folder_that_is_not_a_profile(self, harness: FoldersHarness) -> None:
        folder = harness.root / "documenti"
        harness.folders.create_private_folder(folder)
        harness.put_file(folder, "tesi.docx")

        with pytest.raises(NotAProfileFolderError):
            harness.folders.delete_folder(folder)

        assert harness.folders.inspect(folder).state is FolderState.NOT_EMPTY

    def test_delete_of_a_missing_folder_is_refused(self, harness: FoldersHarness) -> None:
        with pytest.raises(NotAProfileFolderError):
            harness.folders.delete_folder(harness.root / "non-esiste")

    def test_delete_refuses_a_profile_that_is_open_elsewhere(self, harness: FoldersHarness) -> None:
        folder = harness.root / "profilo"
        harness.folders.create_private_folder(folder)
        harness.make_profile_folder(folder)
        lock = harness.folders.acquire_lock(folder)

        with pytest.raises(ProfileInUseError):
            harness.folders.delete_folder(folder)
        assert harness.folders.inspect(folder).state is FolderState.NOT_EMPTY

        lock.release()
        harness.folders.delete_folder(folder)

        assert harness.folders.inspect(folder).state is FolderState.MISSING

    def test_inspect_tells_a_profile_folder_from_any_other_non_empty_folder(
        self, harness: FoldersHarness
    ) -> None:
        profile, other = harness.root / "profilo", harness.root / "altro"
        for folder in (profile, other):
            harness.folders.create_private_folder(folder)
        harness.make_profile_folder(profile)
        harness.put_file(other, "nota.txt")

        assert harness.folders.inspect(profile).has_profile_database
        assert not harness.folders.inspect(other).has_profile_database
        assert not harness.folders.inspect(harness.root / "manca").has_profile_database

    def test_remove_if_empty_removes_an_empty_folder(self, harness: FoldersHarness) -> None:
        folder = harness.root / "vuota"
        harness.folders.create_private_folder(folder)

        assert harness.folders.remove_if_empty(folder)

        assert harness.folders.inspect(folder).state is FolderState.MISSING

    def test_remove_if_empty_ignores_the_lock_file_left_by_a_released_lock(
        self, harness: FoldersHarness
    ) -> None:
        folder = harness.root / "con-lock"
        harness.folders.create_private_folder(folder)
        harness.folders.acquire_lock(folder).release()

        assert harness.folders.remove_if_empty(folder)

        assert harness.folders.inspect(folder).state is FolderState.MISSING

    def test_remove_if_empty_keeps_a_folder_with_anything_else(
        self, harness: FoldersHarness
    ) -> None:
        folder = harness.root / "piena"
        harness.folders.create_private_folder(folder)
        harness.folders.acquire_lock(folder).release()
        harness.put_file(folder, "nota.txt")

        assert not harness.folders.remove_if_empty(folder)

        assert harness.folders.inspect(folder).state is FolderState.NOT_EMPTY

    def test_remove_if_empty_keeps_a_folder_locked_by_someone_else(
        self, harness: FoldersHarness
    ) -> None:
        folder = harness.root / "occupata"
        harness.folders.create_private_folder(folder)
        lock = harness.folders.acquire_lock(folder)

        assert not harness.folders.remove_if_empty(folder)
        assert harness.folders.inspect(folder).state is not FolderState.MISSING

        lock.release()

    def test_remove_if_empty_of_a_missing_folder_does_nothing(
        self, harness: FoldersHarness
    ) -> None:
        assert not harness.folders.remove_if_empty(harness.root / "non-esiste")
