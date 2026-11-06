# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lettura con tetto e scrittura atomica."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.platform.atomic_file import FileTooLargeError, read_limited, write_atomic


def test_write_creates_then_replaces_the_content(tmp_path: Path) -> None:
    target = tmp_path / "dati.json"

    write_atomic(target, b"uno")
    write_atomic(target, b"due")

    assert target.read_bytes() == b"due"
    assert [p.name for p in tmp_path.iterdir()] == ["dati.json"]


def test_a_failed_replace_leaves_no_temporary_and_the_destination_untouched(
    tmp_path: Path,
) -> None:
    target = tmp_path / "dati"
    target.mkdir()  # una cartella non si sostituisce con un file: la rinomina fallisce
    (target / "dentro.txt").write_bytes(b"x")

    with pytest.raises(OSError):
        write_atomic(target, b"nuovo")

    assert (target / "dentro.txt").read_bytes() == b"x"
    assert [p.name for p in tmp_path.iterdir()] == ["dati"]


def test_read_of_a_missing_file_is_none(tmp_path: Path) -> None:
    assert read_limited(tmp_path / "manca", 10) is None


def test_read_at_the_limit_is_accepted_and_one_byte_over_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "f"
    target.write_bytes(b"x" * 10)
    assert read_limited(target, 10) == b"x" * 10

    target.write_bytes(b"x" * 11)
    with pytest.raises(FileTooLargeError):
        read_limited(target, 10)
