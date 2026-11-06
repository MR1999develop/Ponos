# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composizione della finestra dal Composition Root e gesti comuni ai test dell'area di lavoro."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import compose_main_window, parse_command_line
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Activity, FileFormat
from ponos_coach.activities.ports import (
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportScan,
    ImportSource,
)
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.workspace import ImportSources
from tests.fixtures.generators.import_files import fit_run, gpx_run
from tests.fixtures.generators.zip_writer import garmin_download

TODAY = date(2026, 6, 30)
WAIT_MS = 60_000
FILES_IN_FIXTURE_FOLDER = 8  # 5 FIT, 1 GPX, 1 ZIP, 1 FIT in una sottocartella
DAYS_IN_CHART = 61  # dal 1 maggio al 30 giugno 2026


def make_settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def compose(
    tmp_path: Path,
    settings: QSettings,
    *,
    import_sources: ImportSources | None = None,
    gate: threading.Event | None = None,
) -> MainWindow:
    """Finestra dal Composition Root, con configurazione e profili in `tmp_path`.

    `gate` (se dato) fa attendere la decodifica della panoramica finché non è impostato.
    """

    def gated_decode(data: bytes, file_format: FileFormat) -> Activity:
        assert gate is not None
        gate.wait(timeout=30)
        return decode_stored_activity(data, file_format)

    return compose_main_window(
        parse_command_line([]),
        settings=settings,
        config_dir=tmp_path / "config",
        profiles_dir=tmp_path / "profiles",
        system_timezone_id="Europe/Rome",
        clock=lambda _zone: TODAY,
        import_sources=import_sources,
        decode=None if gate is None else gated_decode,
    )


def make_import_folder(tmp_path: Path) -> Path:
    """Cartella con 8 attività valide (FIT, GPX, ZIP, una in sottocartella) e un file rotto."""
    folder = tmp_path / "to-import"
    (folder / "sub").mkdir(parents=True)
    for number in range(5):
        (folder / f"run{number}.fit").write_bytes(fit_run(number))
    (folder / "walk.gpx").write_bytes(gpx_run(10))
    (folder / "garmin.zip").write_bytes(garmin_download(fit_run(20)))
    (folder / "sub" / "deep.fit").write_bytes(fit_run(30))
    (folder / "bad.fit").write_bytes(b"this is not a FIT file")
    return folder


def create_profile(qtbot: QtBot, window: MainWindow, name: str) -> None:
    """Guida il dialogo "Nuovo profilo" (aperto o da aprire) fino a profilo creato e aperto."""
    if window.new_profile_dialog is None:
        window.show_new_profile()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    QTest.keyClicks(dialog.name_edit, name)
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(
        lambda: (
            window.controller.profile is not None
            and window.controller.profile.name.value == name
            and isinstance(window.controller.overview_state, Ready)
        ),
        timeout=WAIT_MS,
    )


def wait_ready(qtbot: QtBot, window: MainWindow, activities: int) -> None:
    """Attende la panoramica pronta con questo numero di attività."""
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and len(window.controller.overview_state.overview.activities) == activities
        ),
        timeout=WAIT_MS,
    )


class GatedSource:
    """Fake di `ImportSource`: ogni lettura attende un cancello, con scadenza."""

    def __init__(self, inner: ImportSource, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:
        return self._inner.scan(is_cancelled)

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        if not self._gate.wait(timeout=30):
            raise ImportEntryError(ImportIssue.UNREADABLE)
        return self._inner.read(entry, max_bytes)

    def close(self) -> None:
        self._inner.close()


def gated_sources(gate: threading.Event) -> ImportSources:
    """Sorgenti vere, con le letture trattenute dal cancello."""
    return ImportSources(
        files=lambda paths: GatedSource(FilesImportSource(list(paths)), gate),
        folder=lambda folder: GatedSource(FolderImportSource(folder), gate),
    )
