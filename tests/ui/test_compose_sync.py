# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composition of the downloaded ZIP extractor: each `zipread` cause becomes a definitive
cause of the journal entry."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from ponos_coach.__main__ import (
    _FAILURE_BY_ZIP_REASON,
    _LazyGarminAccount,
    extract_original_activity,
)
from ponos_coach.activities.domain import RETRYABLE_REASONS, FailureReason, FileFormat
from ponos_coach.activities.sync import OriginalExtractionError
from ponos_coach.zipread import ZipReason
from tests.fakes.garmin import OriginalKind, original_zip


@pytest.mark.parametrize("reason", list(ZipReason))
def test_every_zip_reason_maps_to_a_definitive_failure(reason: ZipReason) -> None:
    failure = _FAILURE_BY_ZIP_REASON[reason]

    assert failure not in RETRYABLE_REASONS
    assert failure is not FailureReason.ATTEMPTS_EXHAUSTED


def test_a_garmin_download_with_a_fit_or_a_gpx_gives_the_file_and_its_format() -> None:
    fit = extract_original_activity(original_zip(1, OriginalKind.FIT))
    gpx = extract_original_activity(original_zip(1, OriginalKind.GPX))

    assert fit.file_format is FileFormat.FIT
    assert gpx.file_format is FileFormat.GPX
    assert fit.content and gpx.content


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (OriginalKind.TCX, FailureReason.UNSUPPORTED_FORMAT),
        (OriginalKind.EMPTY_ZIP, FailureReason.UNSUPPORTED_FORMAT),
        (OriginalKind.TRUNCATED_ZIP, FailureReason.ARCHIVE_INVALID),
        (OriginalKind.NOT_A_ZIP, FailureReason.ARCHIVE_INVALID),
    ],
)
def test_a_download_without_an_activity_file_fails_for_a_definitive_reason(
    kind: OriginalKind, reason: FailureReason
) -> None:
    with pytest.raises(OriginalExtractionError) as error:
        extract_original_activity(original_zip(1, kind))

    assert error.value.reason is reason


_STARTUP_SCRIPT = """
import json, sys, tempfile
from pathlib import Path
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from ponos_coach.__main__ import compose_main_window, parse_command_line

class Quiet:
    def notify(self, title, message):
        pass

app = QApplication([])
with tempfile.TemporaryDirectory() as folder:
    base = Path(folder)
    window = compose_main_window(
        parse_command_line([]),
        settings=QSettings(str(base / "s.ini"), QSettings.Format.IniFormat),
        config_dir=base / "config",
        documents_dir=base / "docs",
        system_timezone_id="UTC",
        log_dir=base / "logs",
        data_dir=base / "data",
        notifier=Quiet(),
    )
    window.start()
    app.processEvents()
    heavy = ("garminconnect", "curl_cffi", "ponos_coach.garmin.connect")
    print(json.dumps([name for name in heavy if name in sys.modules]), flush=True)
    window.close()
    while not window.is_closed:
        app.processEvents()
    window.deleteLater()
    app.processEvents()
"""


def test_starting_the_app_does_not_import_the_garmin_libraries() -> None:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}

    run = subprocess.run(  # noqa: S603 - current interpreter, fixed script
        [sys.executable, "-c", _STARTUP_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )

    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout.strip().splitlines()[-1]) == []


def test_the_default_account_creates_the_real_one_only_when_used() -> None:
    account = _LazyGarminAccount()

    assert account._account is None
