# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contract of `ActivityArchive`: store, read back with verification, failures, temporaries."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES, FileFormat
from ponos_coach.activities.ports import (
    ArchiveFileMissingError,
    ArchiveIntegrityError,
    ArchiveTooLargeError,
)
from tests.contracts.harness import ArchiveHarness

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
CONTENT = b"contenuto originale di prova " * 10


class ActivityArchiveContract:
    """Tests to inherit in a `Test...` class with the `harness` fixture."""

    def test_store_returns_generated_relative_path_hash_and_size(
        self, harness: ArchiveHarness
    ) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)

        assert not archived.relative_path.is_absolute()
        assert archived.relative_path.suffix == ".fit"
        assert "2026" in archived.relative_path.parts
        assert archived.sha256_hex == hashlib.sha256(CONTENT).hexdigest()
        assert archived.size_bytes == len(CONTENT)

    def test_name_does_not_depend_on_anything_but_the_program(
        self, harness: ArchiveHarness
    ) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)

        assert all(part.isascii() for part in archived.relative_path.parts)

    def test_storing_the_same_bytes_twice_is_idempotent(self, harness: ArchiveHarness) -> None:
        first = harness.archive.store(CONTENT, FileFormat.FIT, START)
        second = harness.archive.store(CONTENT, FileFormat.FIT, START)

        assert first == second
        assert harness.archive.read(second, len(CONTENT)) == CONTENT
        assert harness.archive.list_orphan_temporaries() == []

    def test_read_returns_the_original_bytes(self, harness: ArchiveHarness) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)

        assert harness.archive.read(archived, len(CONTENT)) == CONTENT

    def test_read_accepts_exactly_max_bytes_and_refuses_one_less(
        self, harness: ArchiveHarness
    ) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)

        assert harness.archive.read(archived, len(CONTENT)) == CONTENT
        with pytest.raises(ArchiveTooLargeError):
            harness.archive.read(archived, len(CONTENT) - 1)

    def test_content_over_the_global_cap_is_refused(self, harness: ArchiveHarness) -> None:
        with pytest.raises(ArchiveTooLargeError):
            harness.archive.store(bytes(MAX_ACTIVITY_FILE_BYTES + 1), FileFormat.FIT, START)

    def test_altered_file_is_reported_by_read_and_verify(self, harness: ArchiveHarness) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
        harness.corrupt(archived)

        with pytest.raises(ArchiveIntegrityError):
            harness.archive.read(archived, len(CONTENT))
        with pytest.raises(ArchiveIntegrityError):
            harness.archive.verify(archived)

    def test_verify_accepts_an_intact_file(self, harness: ArchiveHarness) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)

        harness.archive.verify(archived)

    def test_missing_file_is_reported_by_read_and_verify(self, harness: ArchiveHarness) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
        harness.delete(archived)

        with pytest.raises(ArchiveFileMissingError):
            harness.archive.read(archived, len(CONTENT))
        with pytest.raises(ArchiveFileMissingError):
            harness.archive.verify(archived)

    def test_orphan_temporaries_are_listed_and_removed_without_touching_files(
        self, harness: ArchiveHarness
    ) -> None:
        archived = harness.archive.store(CONTENT, FileFormat.FIT, START)
        assert harness.archive.list_orphan_temporaries() == []
        harness.plant_orphan_temporary()

        assert len(harness.archive.list_orphan_temporaries()) == 1
        assert harness.archive.remove_orphan_temporaries() == 1
        assert harness.archive.list_orphan_temporaries() == []
        assert harness.archive.remove_orphan_temporaries() == 0
        assert harness.archive.read(archived, len(CONTENT)) == CONTENT
