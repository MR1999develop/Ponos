# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Window composition from the Composition Root and gestures common to the workspace tests."""

from __future__ import annotations

import threading
import weakref
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import compose_main_window, parse_command_line
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Activity, FileFormat, SyncPolicy
from ponos_coach.activities.ports import (
    ActivitySource,
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportScan,
    ImportSource,
)
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.sync_worker import MFA_WAIT_S
from ponos_coach.ui.translations import Language
from ponos_coach.ui.workspace import ImportSources
from tests.fakes.garmin import FakeActivitySource, FakeGarminAccount
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.tiles import FakeTileSource, InMemoryTileKeyStore
from tests.fakes.tokens import InMemoryTokenStore
from tests.fixtures.generators.import_files import fit_run, gpx_run
from tests.fixtures.generators.zip_writer import garmin_download

TODAY = date(2026, 6, 30)
WAIT_MS = 60_000
FILES_IN_FIXTURE_FOLDER = 8  # 5 FIT, 1 GPX, 1 ZIP, 1 FIT in a subfolder
DAYS_IN_CHART = 61  # from 1 May to 30 June 2026


# Garmin pauses and waits nearly zero: tests must not wait seconds for each activity.
FAST_POLICY = SyncPolicy(
    min_pause_s=0.001, max_jitter_s=0.0, backoff_base_s=0.01, backoff_cap_s=0.05
)


@dataclass(frozen=True, slots=True)
class GarminFakes:
    """Fake Garmin, keyring and notification with which `compose` builds the window."""

    source: FakeActivitySource
    account: FakeGarminAccount
    tokens: InMemoryTokenStore
    notifier: RecordingNotifier


def make_fakes(
    *,
    mfa_required: bool = True,
    source: FakeActivitySource | None = None,
    served: ActivitySource | None = None,
) -> GarminFakes:
    """New fakes: no activity on Garmin, no token, no notification.

    `served` is what the account gives to the sync instead of `source` (for example a
    decorator with a gate)."""
    garmin = source or FakeActivitySource([])
    return GarminFakes(
        garmin,
        FakeGarminAccount(served or garmin, mfa_required=mfa_required),
        InMemoryTokenStore(),
        RecordingNotifier(),
    )


@dataclass(frozen=True, slots=True)
class TileFakes:
    """The fake key store and tile source with which `compose` builds the map."""

    keys: InMemoryTileKeyStore
    source: FakeTileSource


def make_tiles() -> TileFakes:
    """New fake tiles: no key saved, so the provider answers "no key"."""
    keys = InMemoryTileKeyStore()
    return TileFakes(keys, FakeTileSource(keys))


_FAKES: weakref.WeakKeyDictionary[MainWindow, GarminFakes] = weakref.WeakKeyDictionary()


def fakes_of(window: MainWindow) -> GarminFakes:
    """The fakes with which `compose` built the window."""
    return _FAKES[window]


def make_settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


class FakeFolderPicker:
    """Fake folder picker: answers with `answer` and remembers where it started."""

    def __init__(self, base: Path) -> None:
        self.base = base  # profiles folder in the tests
        self.answer = base
        self.starts: list[str] = []

    def __call__(self, _parent: QWidget, _title: str, start: str) -> str:
        self.starts.append(start)
        return str(self.answer)


_PICKERS: weakref.WeakKeyDictionary[MainWindow, FakeFolderPicker] = weakref.WeakKeyDictionary()


def picker_of(window: MainWindow) -> FakeFolderPicker:
    """The fake picker with which `compose` built the window."""
    return _PICKERS[window]


def pick_profile_folder(window: MainWindow, dialog: NewProfileDialog, folder: Path) -> None:
    """Choose `folder` with the dialog's "Browse..." (fake picker)."""
    picker_of(window).answer = folder
    QTest.mouseClick(dialog.browse_button, Qt.MouseButton.LeftButton)


def compose(
    tmp_path: Path,
    settings: QSettings,
    *,
    import_sources: ImportSources | None = None,
    gate: threading.Event | None = None,
    log_dir: Path | None = None,
    fakes: GarminFakes | None = None,
    default_token_store: bool = False,
    mfa_wait_s: float = MFA_WAIT_S,
    language: Language = Language.ENGLISH,
    tiles: TileFakes | None = None,
) -> MainWindow:
    """Window from the Composition Root, with configuration and profiles in `tmp_path`.

    `gate` (if given) makes the overview decoding wait until it is set. The map uses fake tiles
    (no network, no real key store, no cache on disk): `tiles` lets a test keep them.
    """

    def gated_decode(data: bytes, file_format: FileFormat) -> Activity:
        assert gate is not None
        gate.wait(timeout=30)
        return decode_stored_activity(data, file_format)

    picker = FakeFolderPicker(tmp_path / "profiles")
    garmin = fakes or make_fakes()
    map_tiles = tiles or make_tiles()
    window = compose_main_window(
        parse_command_line([]),
        settings=settings,
        config_dir=tmp_path / "config",
        documents_dir=tmp_path / "documents",
        folder_picker=picker,
        system_timezone_id="Europe/Rome",
        clock=lambda _zone: TODAY,
        import_sources=import_sources,
        decode=None if gate is None else gated_decode,
        log_dir=tmp_path / "logs" if log_dir is None else log_dir,
        garmin_account=garmin.account,
        token_store=None if default_token_store else garmin.tokens,
        notifier=garmin.notifier,
        sync_policy=FAST_POLICY,
        data_dir=tmp_path / "data",
        mfa_wait_s=mfa_wait_s,
        language=language,
        map_cache_dir=tmp_path / "map-cache",
        tile_key_store=map_tiles.keys,
        tile_source=map_tiles.source,
    )
    _PICKERS[window] = picker
    _FAKES[window] = garmin
    return window


def close_window(qtbot: QtBot, window: MainWindow) -> None:
    """Close the window and wait for the end of the two-phase closing (profile closed)."""
    window.close()
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)


def make_import_folder(tmp_path: Path) -> Path:
    """Folder with 8 valid activities (FIT, GPX, ZIP, one in a subfolder) and a broken file."""
    folder = tmp_path / "to-import"
    (folder / "sub").mkdir(parents=True)
    for number in range(5):
        (folder / f"run{number}.fit").write_bytes(fit_run(number))
    (folder / "walk.gpx").write_bytes(gpx_run(10))
    (folder / "garmin.zip").write_bytes(garmin_download(fit_run(20)))
    (folder / "sub" / "deep.fit").write_bytes(fit_run(30))
    (folder / "bad.fit").write_bytes(b"this is not a FIT file")
    return folder


def fill_body_data(
    dialog: NewProfileDialog,
    height: str = "170",
    weight: str = "65,5",
    birth: date = date(1995, 4, 2),
) -> None:
    """Write height, weight and date of birth (test values, of no real person)."""
    QTest.keyClicks(dialog.height_edit, height)
    QTest.keyClicks(dialog.weight_edit, weight)
    dialog.birth_edit.select(birth)


def fill_new_profile(window: MainWindow, dialog: NewProfileDialog, name: str) -> None:
    """Write name and body data and choose the folder `<profiles>/<name>` (fake picker)."""
    QTest.keyClicks(dialog.name_edit, name)
    fill_body_data(dialog)
    pick_profile_folder(window, dialog, picker_of(window).base / name)


def create_profile(qtbot: QtBot, window: MainWindow, name: str) -> None:
    """Drive the "New profile" dialog (open or to be opened) to a created and opened profile."""
    if window.new_profile_dialog is None:
        window.show_new_profile()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    fill_new_profile(window, dialog, name)
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
    """Wait for the overview ready with this number of activities."""
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and len(window.controller.overview_state.overview.activities) == activities
        ),
        timeout=WAIT_MS,
    )


class GatedSource:
    """Fake of `ImportSource`: each read waits at a gate, with a deadline."""

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
    """Real sources, with the reads held by the gate."""
    return ImportSources(
        files=lambda paths: GatedSource(FilesImportSource(list(paths)), gate),
        folder=lambda folder: GatedSource(FolderImportSource(folder), gate),
    )
