# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Settings > Map: the cache size and "Clear map cache" (model and dialog).

The tile source and the cache are the fakes of `tests/fakes/tiles.py`: no network.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.maps.domain import DEFAULT_CACHE_BYTES, GIB, TileAddress
from ponos_coach.maps.ports import TileCacheError, TileImage
from ponos_coach.maps.use_cases import TileProxy
from ponos_coach.ui.map_settings import KEY_CACHE_GIB, MapSettingsModel
from ponos_coach.ui.map_settings_dialog import MapSettingsDialog
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, make_png, serving
from tests.ui.workspace_support import WAIT_MS, close_window, compose, make_settings


def model_with(tmp_path: Path) -> tuple[MapSettingsModel, InMemoryTileCache, QSettings]:
    source = FakeTileSource()
    cache = InMemoryTileCache(DEFAULT_CACHE_BYTES)
    proxy = TileProxy(serving(source), cache, lambda: datetime.now(UTC), time.monotonic)
    settings = QSettings(str(tmp_path / "map.ini"), QSettings.Format.IniFormat)
    return MapSettingsModel(proxy, cache, settings), cache, settings


# --- Model: the cache -----------------------------------------------------------------------


def test_the_cache_size_defaults_to_four_gb_and_is_remembered_in_gb(tmp_path: Path) -> None:
    model, cache, settings = model_with(tmp_path)
    assert model.cache_limit().bytes == DEFAULT_CACHE_BYTES == 4 * GIB

    result = model.set_cache_limit("2,5")

    assert result.limit is not None
    assert result.limit.bytes == int(2.5 * GIB)
    assert settings.value(KEY_CACHE_GIB) == "2.5"
    assert model.cache_limit().bytes == int(2.5 * GIB)
    assert cache.used_bytes() == 0  # nothing stored, nothing taken


@pytest.mark.parametrize("text", ["", "abc", "0", "0.01", "100", "-4", "1e3", "4 GB", "9" * 40])
def test_a_wrong_cache_size_changes_nothing(tmp_path: Path, text: str) -> None:
    model, _cache, settings = model_with(tmp_path)

    assert model.set_cache_limit(text).limit is None

    assert settings.value(KEY_CACHE_GIB) is None
    assert model.cache_limit().bytes == DEFAULT_CACHE_BYTES


def test_lowering_the_size_drops_the_least_recently_used_tiles_at_once(tmp_path: Path) -> None:
    _model, cache, _settings = model_with(tmp_path)
    now = datetime.now(UTC)
    for x in range(6):
        cache.put(TileAddress(3, x, 0), TileImage(make_png(extra_bytes=100_000), 3600), now)
    before = cache.used_bytes()

    cache.set_limit(before // 2)  # what the model does with a smaller size
    assert cache.used_bytes() <= before // 2


def test_clearing_the_cache_reports_what_it_freed(tmp_path: Path) -> None:
    model, cache, _settings = model_with(tmp_path)
    cache.put(TileAddress(3, 1, 1), TileImage(make_png(), 3600), datetime.now(UTC))
    used = model.cache_used_bytes()
    assert used > 0

    assert model.clear_cache() == used

    assert model.cache_used_bytes() == 0


# --- Dialog ---------------------------------------------------------------------------------


def make_dialog(
    qtbot: QtBot, tmp_path: Path
) -> tuple[MapSettingsDialog, MapSettingsModel, InMemoryTileCache]:
    model, cache, _settings = model_with(tmp_path)
    dialog = MapSettingsDialog(model)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, model, cache


def test_the_dialog_shows_the_default_cache_size_and_no_key_field(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, _model, _cache = make_dialog(qtbot, tmp_path)

    assert dialog.cache_field.text() == "4"
    assert dialog.findChild(QWidget, "keyField") is None  # the map needs no key any more


def test_the_cache_size_applies_when_the_field_is_left_and_a_wrong_one_is_explained(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, _cache = make_dialog(qtbot, tmp_path)

    dialog.cache_field.setText("0,5")
    dialog.cache_field.editingFinished.emit()
    assert model.cache_limit().bytes == GIB // 2

    dialog.cache_field.setText("lots")
    dialog.cache_field.editingFinished.emit()
    assert "Enter a size in GB between" in dialog.cache_message_text
    assert model.cache_limit().bytes == GIB // 2  # unchanged


def push_tile(cache: InMemoryTileCache) -> int:
    cache.put(
        TileAddress(4, 2, 2), TileImage(make_png(extra_bytes=50_000), 3600), datetime.now(UTC)
    )
    return cache.used_bytes()


def answer_clear(dialog: MapSettingsDialog, button_text: str) -> QMessageBox:
    box = dialog.open_boxes[0]
    next(b for b in box.buttons() if b.text() == button_text).click()
    return box


def test_clear_cache_asks_first_says_how_much_it_frees_and_the_button_names_the_action(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, _model, cache = make_dialog(qtbot, tmp_path)
    used = push_tile(cache)

    dialog.clear_button.click()

    qtbot.waitUntil(lambda: bool(dialog.open_boxes), timeout=WAIT_MS)
    box = dialog.open_boxes[0]
    assert "frees" in box.text()
    assert "downloads the tiles again" in box.informativeText()  # the cost, said up front
    labels = sorted(b.text() for b in box.buttons())
    assert labels == ["Clear cache", "Keep cache"]  # verbs, never OK and Cancel
    assert box.defaultButton().text() == "Keep cache"
    assert cache.used_bytes() == used  # asking changed nothing


def test_keeping_the_cache_leaves_it_and_clearing_empties_it(qtbot: QtBot, tmp_path: Path) -> None:
    dialog, _model, cache = make_dialog(qtbot, tmp_path)
    used = push_tile(cache)

    dialog.ask_clear_cache()
    answer_clear(dialog, "Keep cache")
    assert cache.used_bytes() == used

    cleared: list[object] = []
    dialog.cache_cleared.connect(lambda: cleared.append(1))
    dialog.ask_clear_cache()
    answer_clear(dialog, "Clear cache")
    assert cache.used_bytes() == 0
    assert cleared == [1]


# --- A cache that cannot be opened (acceptance campaign of 2026-11-11) -------------------------


class UnopenableTileCache(InMemoryTileCache):
    """A cache whose file cannot be opened (a folder that is a file, a disk that refuses)."""

    def used_bytes(self) -> int:
        raise TileCacheError("tile cache cannot be opened")

    def clear(self) -> None:
        raise TileCacheError("tile cache cannot be opened")


def test_a_cache_that_cannot_be_opened_is_said_and_cannot_be_cleared(
    qtbot: QtBot, tmp_path: Path
) -> None:
    cache = UnopenableTileCache(DEFAULT_CACHE_BYTES)
    proxy = TileProxy(serving(FakeTileSource()), cache, lambda: datetime.now(UTC), time.monotonic)
    settings = QSettings(str(tmp_path / "map.ini"), QSettings.Format.IniFormat)
    model = MapSettingsModel(proxy, cache, settings)

    dialog = MapSettingsDialog(model)  # building the page must not raise
    qtbot.addWidget(dialog)

    assert model.cache_used_bytes() is None
    assert model.clear_cache() is None
    assert dialog.cache_message_text.startswith("The map cache cannot be opened")
    assert not dialog.clear_button.isEnabled()
    assert dialog.clear_button.toolTip()  # the reason, where the user looks


def test_the_window_opens_when_the_map_cache_folder_is_unusable(
    qtbot: QtBot, tmp_path: Path
) -> None:
    # The folder of the map cache is a file: the window opens, the map works without the cache.
    (tmp_path / "map-cache").write_bytes(b"not a folder")
    window = compose(tmp_path, make_settings(tmp_path))
    qtbot.addWidget(window)

    page = window.map_ui.settings_dialog
    assert page is not None
    assert page.cache_message_text.startswith("The map cache cannot be opened")
    close_window(qtbot, window)
