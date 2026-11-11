# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The removal of leftover GPX files reports the failures it skips, without paths."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from ponos_coach.storage import archive_cleanup
from ponos_coach.storage.archive_cleanup import remove_gpx_files

_FIRST = "2026/" + "aa" * 32 + ".gpx"
_SECOND = "2026/" + "bb" * 32 + ".gpx"


def _archive(tmp_path: Path, *names: str) -> Path:
    root = tmp_path / "archive"
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"gpx")
    return root


def test_a_removal_that_fails_is_counted_in_the_log_without_any_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    root = _archive(tmp_path, _FIRST, _SECOND)
    real_unlink = Path.unlink

    def refuse_the_first(self: Path, *args: object, **kwargs: object) -> None:
        if self.name == _FIRST.split("/")[1]:
            raise PermissionError("locked")
        real_unlink(self)

    monkeypatch.setattr(Path, "unlink", refuse_the_first)
    with caplog.at_level(logging.WARNING, logger=archive_cleanup.__name__):
        removed = remove_gpx_files(root, [_FIRST, _SECOND])
    assert removed == 1
    assert [r.getMessage() for r in caplog.records] == [
        "1 leftover GPX file(s) could not be removed"
    ]
    assert "aa" * 8 not in caplog.text
    assert str(tmp_path) not in caplog.text


def test_a_file_already_missing_is_not_a_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = _archive(tmp_path)
    with caplog.at_level(logging.WARNING, logger=archive_cleanup.__name__):
        assert remove_gpx_files(root, [_FIRST]) == 0
    assert caplog.records == []
