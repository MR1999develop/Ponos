# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Backup manifest: closed list of names, limits, round trip and hostile documents (T-DATI-05)."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId
from ponos_coach.profiles.domain.backup import (
    BACKUP_FORMAT,
    BACKUP_FORMAT_VERSION,
    BackupFile,
    BackupLimits,
    BackupManifest,
    ManifestError,
    ManifestTooNewError,
    decode_manifest,
    encode_manifest,
    is_backup_entry_path,
)

_ID = ProfileId(uuid.UUID("5f0e8d6c-3b7a-4c1e-9a52-0d1f6b7e8c90"))
_CREATED = datetime(2026, 11, 8, 10, 30, tzinfo=UTC)
_SHA = hashlib.sha256(b"x").hexdigest()


def _archive_path(seed: int, extension: str = "fit") -> str:
    digest = hashlib.sha256(str(seed).encode()).hexdigest()
    return f"archivio/2026/{digest}.{extension}"


def _manifest(*archive_seeds: int) -> BackupManifest:
    files = [BackupFile(PROFILE_DATABASE_NAME, 4096, _SHA)]
    files += [BackupFile(_archive_path(seed), 100 + seed, _SHA) for seed in archive_seeds]
    ordered = tuple(sorted(files, key=lambda file: file.path))  # the encoding sorts by path
    return BackupManifest(BACKUP_FORMAT_VERSION, _CREATED, "0.1.0", _ID, 7, ordered)


def _document(**changes: object) -> bytes:
    document = json.loads(encode_manifest(_manifest(1, 2)))
    document.update(changes)
    return json.dumps(document).encode()


class TestEntryNames:
    @pytest.mark.parametrize(
        "name",
        [PROFILE_DATABASE_NAME, _archive_path(1), _archive_path(2, "gpx")],
    )
    def test_names_of_a_profile_are_accepted(self, name: str) -> None:
        assert is_backup_entry_path(name)

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "../ponos.sqlite3",
            "archivio\\2026\\" + "a" * 64 + ".fit",
            "archivio/26/" + "a" * 64 + ".fit",
            "archivio/2026/" + "a" * 63 + ".fit",
            "archivio/2026/" + "a" * 64 + ".tcx",
            "archivio/2026/" + "a" * 64 + ".fit\n",
            "ponos.sqlite3-wal",
            "PONOS.SQLITE3",
        ],
    )
    def test_anything_else_is_refused(self, name: str) -> None:
        assert not is_backup_entry_path(name)


class TestManifestInvariants:
    def test_exactly_one_database(self) -> None:
        with pytest.raises(ValueError, match="exactly one database"):
            BackupManifest(1, _CREATED, "0.1.0", _ID, 1, (BackupFile(_archive_path(1), 1, _SHA),))

    def test_duplicate_paths_are_refused(self) -> None:
        database = BackupFile(PROFILE_DATABASE_NAME, 1, _SHA)
        with pytest.raises(ValueError, match="duplicate"):
            BackupManifest(1, _CREATED, "0.1.0", _ID, 1, (database, database))

    def test_creation_must_be_utc(self) -> None:
        database = BackupFile(PROFILE_DATABASE_NAME, 1, _SHA)
        local = datetime(2026, 11, 8, tzinfo=timezone(timedelta(hours=1)))
        with pytest.raises(ValueError, match="UTC"):
            BackupManifest(1, local, "0.1.0", _ID, 1, (database,))

    def test_file_entry_validates_size_and_hash(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            BackupFile(PROFILE_DATABASE_NAME, -1, _SHA)
        with pytest.raises(ValueError, match="SHA-256"):
            BackupFile(PROFILE_DATABASE_NAME, 1, "abc")


class TestRoundTrip:
    def test_encode_then_decode_gives_the_same_manifest(self) -> None:
        manifest = _manifest(1, 2, 3)
        assert decode_manifest(encode_manifest(manifest)) == manifest

    def test_the_created_instant_is_written_in_utc_with_z(self) -> None:
        assert json.loads(encode_manifest(_manifest()))["created_utc"] == "2026-11-08T10:30:00Z"

    @given(st.sets(st.integers(0, 10_000), max_size=40))
    def test_any_set_of_files_survives(self, seeds: set[int]) -> None:
        manifest = _manifest(*sorted(seeds))
        decoded = decode_manifest(encode_manifest(manifest))
        assert {f.path for f in decoded.files} == {f.path for f in manifest.files}
        assert decoded.total_bytes == manifest.total_bytes

    def test_unknown_keys_are_ignored(self) -> None:
        assert decode_manifest(_document(future_field=[1, 2])) == _manifest(1, 2)


class TestHostileManifests:
    def test_newer_format_is_a_distinct_error(self) -> None:
        with pytest.raises(ManifestTooNewError):
            decode_manifest(_document(format_version=BACKUP_FORMAT_VERSION + 1))

    @pytest.mark.parametrize(
        "changes",
        [
            {"format": "something-else"},
            {"format_version": True},
            {"created_utc": "2026-11-08T10:30:00+01:00"},
            {"app_version": "x" * 33},
            {"profile_id": "not-a-uuid"},
            {"schema_version": 0},
            {"files": "none"},
            {"files": [{"path": "../x", "size_bytes": 1, "sha256": _SHA}]},
            {"files": [{"path": PROFILE_DATABASE_NAME, "size_bytes": 1.5, "sha256": _SHA}]},
            {"files": []},
        ],
    )
    def test_invalid_fields_are_refused(self, changes: dict[str, object]) -> None:
        with pytest.raises(ManifestError):
            decode_manifest(_document(**changes))

    @pytest.mark.parametrize("data", [b"", b"[]", b"null", b"{", b"\xff\xfe", b'{"format": 1}'])
    def test_malformed_documents_are_refused(self, data: bytes) -> None:
        with pytest.raises(ManifestError):
            decode_manifest(data)

    def test_deeply_nested_document_is_refused_not_crashed(self) -> None:
        with pytest.raises(ManifestError):
            decode_manifest(b"[" * 100_000 + b"]" * 100_000)

    def test_size_is_checked_before_parsing(self) -> None:
        limits = BackupLimits(max_manifest_bytes=64)
        with pytest.raises(ManifestError, match="too large"):
            decode_manifest(encode_manifest(_manifest(1, 2)), limits)

    def test_too_many_files(self) -> None:
        with pytest.raises(ManifestError, match="too long"):
            decode_manifest(encode_manifest(_manifest(1, 2, 3)), BackupLimits(max_entries=3))

    def test_file_and_total_size_limits(self) -> None:
        data = encode_manifest(_manifest(1))
        with pytest.raises(ManifestError, match="size limit"):
            decode_manifest(data, BackupLimits(max_file_bytes=50))
        with pytest.raises(ManifestError, match="total"):
            decode_manifest(data, BackupLimits(max_total_bytes=4096))

    def test_limits_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            BackupLimits(max_entries=0)

    def test_format_name_is_stable(self) -> None:
        assert BACKUP_FORMAT == "ponos-coach-profile-backup"
