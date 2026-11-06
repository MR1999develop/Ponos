# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Estrazione sicura di una voce di attività da uno ZIP dato come byte (T-SEC-02, T-IMP-04)."""

from __future__ import annotations

import contextlib
import io
import zipfile

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.zipread import (
    ZipLimits,
    ZipReadError,
    ZipReason,
    is_safe_entry_name,
    read_activity_entry,
)
from tests.fixtures.generators import zip_writer as zw

PAYLOAD = b"contenuto-di-prova" * 20
MIB = 1024 * 1024


def reason_of(content: bytes, limits: ZipLimits | None = None) -> ZipReason:
    with pytest.raises(ZipReadError) as caught:
        read_activity_entry(content, limits)
    return caught.value.reason


class TestValidArchives:
    def test_single_fit_entry_is_extracted_with_its_suffix(self) -> None:
        result = read_activity_entry(zw.build_zip([("123_ACTIVITY.fit", PAYLOAD)]))

        assert (result.suffix, result.content) == (".fit", PAYLOAD)

    def test_single_gpx_entry_in_a_subfolder_with_upper_case_suffix(self) -> None:
        result = read_activity_entry(zw.build_zip([("cartella/Corsa.GPX", PAYLOAD)]))

        assert (result.suffix, result.content) == (".gpx", PAYLOAD)

    def test_stored_entries_and_other_files_are_accepted_and_ignored(self) -> None:
        archive = zw.build_zip(
            [("leggimi.txt", b"x"), ("a.fit", PAYLOAD), ("cartella/", b"")],
            method=zipfile.ZIP_STORED,
        )

        assert read_activity_entry(archive).content == PAYLOAD

    def test_entry_exactly_at_the_size_limit_is_accepted(self) -> None:
        archive = zw.build_zip([("a.fit", bytes(range(256)) * 4)])

        assert len(read_activity_entry(archive, ZipLimits(max_entry_bytes=1024)).content) == 1024


class TestRejectedArchives:
    def test_not_a_zip(self) -> None:
        assert reason_of(b"questo non e uno zip" * 10) is ZipReason.ARCHIVE_INVALID

    def test_empty_input(self) -> None:
        assert reason_of(b"") is ZipReason.ARCHIVE_INVALID

    def test_truncated_zip(self) -> None:
        assert reason_of(zw.build_zip([("a.fit", PAYLOAD)])[:-40]) is ZipReason.ARCHIVE_INVALID

    def test_zip_without_entries(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w"):
            pass

        assert reason_of(buffer.getvalue()) is ZipReason.NO_ACTIVITY

    def test_zip_without_activity_entry(self) -> None:
        assert reason_of(zw.build_zip([("a.tcx", PAYLOAD)])) is ZipReason.NO_ACTIVITY

    def test_two_activity_entries(self) -> None:
        archive = zw.build_zip([("a.fit", PAYLOAD), ("b.gpx", PAYLOAD)])

        assert reason_of(archive) is ZipReason.MULTIPLE_ACTIVITIES

    def test_nested_zip(self) -> None:
        inner = zw.build_zip([("a.fit", PAYLOAD)])

        assert reason_of(zw.build_zip([("inner.zip", inner)])) is ZipReason.NESTED_ARCHIVE

    @pytest.mark.parametrize(
        "name",
        ["../a.fit", "/a.fit", "\\a.fit", "C:/a.fit", "x/../a.fit", "x\\..\\a.fit", "CON.fit"],
    )
    def test_unsafe_entry_names(self, name: str) -> None:
        assert reason_of(zw.build_zip([(name, PAYLOAD)])) is ZipReason.UNSAFE_NAME

    def test_encrypted_entry(self) -> None:
        archive = zw.patch_entry(zw.build_zip([("a.fit", PAYLOAD)]), 0, flags=0x1)

        assert reason_of(archive) is ZipReason.ENCRYPTED

    def test_declared_size_over_the_entry_limit_is_refused_before_extracting(self) -> None:
        archive = zw.build_zip([("a.fit", bytes(2000))], method=zipfile.ZIP_STORED)

        assert reason_of(archive, ZipLimits(max_entry_bytes=1999)) is ZipReason.TOO_LARGE

    def test_compression_bomb_over_the_ratio(self) -> None:
        assert reason_of(zw.zero_bomb(2 * MIB, "bomb.fit")) is ZipReason.ARCHIVE_LIMIT

    def test_ratio_is_configurable(self) -> None:
        archive = zw.zero_bomb(2 * MIB, "bomb.fit")

        assert read_activity_entry(archive, ZipLimits(max_ratio=100_000)).suffix == ".fit"

    def test_too_many_entries(self) -> None:
        assert reason_of(zw.many_entries(11), ZipLimits(max_entries=10)) is ZipReason.ARCHIVE_LIMIT

    def test_entry_count_exactly_at_the_limit_is_accepted(self) -> None:
        archive = zw.build_zip([("a.fit", PAYLOAD)] + [(f"n{i}.txt", b"") for i in range(9)])

        assert read_activity_entry(archive, ZipLimits(max_entries=10)).content == PAYLOAD

    def test_total_declared_size_over_the_limit(self) -> None:
        archive = zw.build_zip([("a.fit", bytes(600)), ("b.txt", bytes(600))])

        assert reason_of(archive, ZipLimits(max_total_bytes=1000)) is ZipReason.ARCHIVE_LIMIT

    def test_wrong_crc(self) -> None:
        archive = zw.patch_entry(zw.build_zip([("a.fit", PAYLOAD)]), 0, crc=0xDEADBEEF)

        assert reason_of(archive) is ZipReason.ARCHIVE_INVALID

    def test_declared_size_smaller_than_the_real_one(self) -> None:
        archive = zw.build_zip([("a.fit", PAYLOAD)], method=zipfile.ZIP_STORED)
        patched = zw.patch_entry(archive, 0, file_size=len(PAYLOAD) - 5)

        assert reason_of(patched) is ZipReason.ARCHIVE_INVALID

    def test_declared_size_larger_than_the_real_one(self) -> None:
        archive = zw.build_zip([("a.fit", PAYLOAD)])
        patched = zw.patch_entry(archive, 0, file_size=len(PAYLOAD) + 50)

        assert reason_of(patched) is ZipReason.ARCHIVE_INVALID

    def test_duplicate_names_differing_in_case(self) -> None:
        assert reason_of(zw.build_zip([("A.fit", PAYLOAD), ("a.FIT", PAYLOAD)])) is (
            ZipReason.ARCHIVE_INVALID
        )

    def test_error_message_is_only_the_reason(self) -> None:
        with pytest.raises(ZipReadError, match=r"^unsafe_name$"):
            read_activity_entry(zw.build_zip([("../segreto.fit", PAYLOAD)]))


class TestLimits:
    @pytest.mark.parametrize(
        "field", ["max_entries", "max_total_bytes", "max_ratio", "max_directory_bytes"]
    )
    def test_limits_must_be_positive(self, field: str) -> None:
        with pytest.raises(ValueError, match=field):
            ZipLimits(**{field: 0})


class TestInvariants:
    @given(st.binary(max_size=2000))
    def test_arbitrary_bytes_never_escape_as_other_exceptions(self, data: bytes) -> None:
        with contextlib.suppress(ZipReadError):
            read_activity_entry(data)

    @given(st.integers(min_value=0, max_value=200), st.integers(min_value=0, max_value=255))
    def test_single_byte_corruption_is_rejected_or_returns_the_payload(
        self, position: int, value: int
    ) -> None:
        archive = bytearray(zw.build_zip([("a.fit", PAYLOAD)]))
        archive[position % len(archive)] = value
        try:
            result = read_activity_entry(bytes(archive))
        except ZipReadError:
            return
        assert result.content == PAYLOAD

    @given(st.text(max_size=40))
    def test_safe_names_have_no_traversal_or_control_characters(self, name: str) -> None:
        if is_safe_entry_name(name):
            parts = name.replace("\\", "/").split("/")
            assert ".." not in parts
            assert not name.startswith(("/", "\\"))
            assert all(ord(c) >= 32 for c in name)
