# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Settings > Map: the key, the cache size and "Clear map cache" (model and dialog).

The provider, the key store and the cache are the fakes of `tests/fakes/tiles.py`: no network, no
real key store. The keys in the tests are made up.
"""

from __future__ import annotations

import gc
import threading
import time
import weakref
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QLineEdit, QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.maps.domain import DEFAULT_CACHE_BYTES, GIB, TileAddress, TileKey
from ponos_coach.maps.ports import TileImage, TileProblem, TileResult, TileUnavailable
from ponos_coach.maps.use_cases import TileKeySettings, TileProxy
from ponos_coach.ui.map_settings import KEY_CACHE_GIB, KeyOutcome, MapSettingsModel
from ponos_coach.ui.map_settings_dialog import MapSettingsDialog
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, InMemoryTileKeyStore, make_png
from tests.ui.workspace_support import WAIT_MS

GOOD = FakeTileSource.ACCEPTED_KEY
OTHER_VALID = "otra-chiave-di-prova-12345"  # valid in form, refused by the fake provider


def model_with(
    tmp_path: Path, source_script: Sequence[TileResult] | None = None
) -> tuple[MapSettingsModel, InMemoryTileKeyStore, InMemoryTileCache, QSettings, FakeTileSource]:
    keys = InMemoryTileKeyStore()
    source = FakeTileSource(keys, script=source_script)
    cache = InMemoryTileCache(DEFAULT_CACHE_BYTES)
    proxy = TileProxy(source, cache, lambda: datetime.now(UTC), time.monotonic)
    settings = QSettings(str(tmp_path / "map.ini"), QSettings.Format.IniFormat)
    model = MapSettingsModel(TileKeySettings(keys, source, proxy), proxy, cache, settings)
    return model, keys, cache, settings, source


def save_and_wait(qtbot: QtBot, model: MapSettingsModel, text: str) -> KeyOutcome:
    with qtbot.waitSignal(model.key_finished, timeout=WAIT_MS) as blocker:
        assert model.save_key(text)
    outcome = blocker.args[0]
    assert isinstance(outcome, KeyOutcome)
    return outcome


# --- Model: the key -------------------------------------------------------------------------


def test_a_key_the_provider_accepts_is_saved(qtbot: QtBot, tmp_path: Path) -> None:
    model, keys, _cache, _settings, _source = model_with(tmp_path)

    assert save_and_wait(qtbot, model, f"  {GOOD}\n") is KeyOutcome.ACCEPTED  # spaces are ignored

    assert keys.key == TileKey(GOOD)
    assert model.has_key()
    model.close()


def test_a_refused_key_is_not_kept_and_the_previous_one_stays(qtbot: QtBot, tmp_path: Path) -> None:
    model, keys, _cache, _settings, _source = model_with(tmp_path)
    keys.save(TileKey(GOOD))

    assert save_and_wait(qtbot, model, OTHER_VALID) is KeyOutcome.REJECTED

    assert keys.key == TileKey(GOOD)
    model.close()


def test_a_text_that_is_not_a_key_is_refused_before_asking_the_provider(
    qtbot: QtBot, tmp_path: Path
) -> None:
    model, keys, _cache, _settings, source = model_with(tmp_path)

    for text in ("", "short", "has spaces in it 123", "x" * 200, "ключ-ключ-ключ"):
        assert save_and_wait(qtbot, model, text) is KeyOutcome.INVALID_FORMAT

    assert keys.key is None
    assert source.calls == []
    model.close()


def test_a_key_saved_while_the_provider_cannot_be_asked_is_kept_and_says_so(
    qtbot: QtBot, tmp_path: Path
) -> None:
    script = [TileUnavailable(TileProblem.NETWORK_UNAVAILABLE)]
    model, keys, _cache, _settings, _source = model_with(tmp_path, script)

    assert save_and_wait(qtbot, model, GOOD) is KeyOutcome.SAVED_UNVERIFIED

    assert keys.key == TileKey(GOOD)
    model.close()


def test_without_a_system_key_store_the_outcome_says_so(qtbot: QtBot, tmp_path: Path) -> None:
    model, keys, _cache, _settings, _source = model_with(tmp_path)
    keys.unavailable = True

    assert save_and_wait(qtbot, model, GOOD) is KeyOutcome.STORE_UNAVAILABLE
    assert model.remove_key() is KeyOutcome.STORE_UNAVAILABLE
    assert not model.has_key()
    model.close()


def test_removing_the_key_forgets_it_and_the_check_runs_one_at_a_time(
    qtbot: QtBot, tmp_path: Path
) -> None:
    model, keys, _cache, _settings, _source = model_with(tmp_path)
    keys.save(TileKey(GOOD))

    assert model.remove_key() is KeyOutcome.REMOVED
    assert keys.key is None
    assert model.save_key(GOOD)
    assert model.is_checking
    assert not model.save_key(GOOD)  # a second check while the first runs is refused
    qtbot.waitUntil(lambda: not model.is_checking, timeout=WAIT_MS)
    model.close()
    assert not model.save_key(GOOD)  # closed: nothing starts


def test_a_check_still_running_after_close_keeps_no_reference_to_the_model(
    qtbot: QtBot, tmp_path: Path
) -> None:
    # The last reference to the QObject must never be dropped on the worker thread (see
    # `ui/worker_handover.py`): once closed and released, the model is gone at once.
    gate = threading.Event()
    started = threading.Event()
    keys = InMemoryTileKeyStore()

    class Blocked(FakeTileSource):
        def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:
            started.set()
            gate.wait(timeout=30)
            return super().fetch(z, x, y, language)

    source = Blocked(keys)
    proxy = TileProxy(source, InMemoryTileCache(), lambda: datetime.now(UTC), time.monotonic)
    settings = QSettings(str(tmp_path / "map.ini"), QSettings.Format.IniFormat)
    model = MapSettingsModel(
        TileKeySettings(keys, source, proxy), proxy, InMemoryTileCache(), settings
    )
    try:
        assert model.save_key(GOOD)
        assert started.wait(timeout=WAIT_MS / 1000)
        model.close()
        released = weakref.ref(model)
        del model
        gc.collect()

        assert released() is None
    finally:
        gate.set()


# --- Model: the cache -----------------------------------------------------------------------


def test_the_cache_size_defaults_to_four_gb_and_is_remembered_in_gb(tmp_path: Path) -> None:
    model, _keys, cache, settings, _source = model_with(tmp_path)
    assert model.cache_limit().bytes == DEFAULT_CACHE_BYTES == 4 * GIB

    result = model.set_cache_limit("2,5")

    assert result.limit is not None
    assert result.limit.bytes == int(2.5 * GIB)
    assert settings.value(KEY_CACHE_GIB) == "2.5"
    assert model.cache_limit().bytes == int(2.5 * GIB)
    assert cache.used_bytes() == 0  # nothing stored, nothing taken
    model.close()


@pytest.mark.parametrize("text", ["", "abc", "0", "0.01", "100", "-4", "1e3", "4 GB", "9" * 40])
def test_a_wrong_cache_size_changes_nothing(tmp_path: Path, text: str) -> None:
    model, _keys, _cache, settings, _source = model_with(tmp_path)

    assert model.set_cache_limit(text).limit is None

    assert settings.value(KEY_CACHE_GIB) is None
    assert model.cache_limit().bytes == DEFAULT_CACHE_BYTES
    model.close()


def test_lowering_the_size_drops_the_least_recently_used_tiles_at_once(tmp_path: Path) -> None:
    model, _keys, cache, _settings, _source = model_with(tmp_path)
    now = datetime.now(UTC)
    for x in range(6):
        cache.put("en", TileAddress(3, x, 0), TileImage(make_png(extra_bytes=100_000), 3600), now)
    before = cache.used_bytes()

    cache.set_limit(before // 2)  # what the model does with a smaller size
    assert cache.used_bytes() <= before // 2
    model.close()


def test_clearing_the_cache_reports_what_it_freed(tmp_path: Path) -> None:
    model, _keys, cache, _settings, _source = model_with(tmp_path)
    cache.put("en", TileAddress(3, 1, 1), TileImage(make_png(), 3600), datetime.now(UTC))
    used = model.cache_used_bytes()
    assert used > 0

    assert model.clear_cache() == used

    assert model.cache_used_bytes() == 0
    model.close()


# --- Dialog ---------------------------------------------------------------------------------


def make_dialog(
    qtbot: QtBot, tmp_path: Path, script: Sequence[TileResult] | None = None
) -> tuple[MapSettingsDialog, MapSettingsModel, InMemoryTileKeyStore, InMemoryTileCache]:
    model, keys, cache, _settings, _source = model_with(tmp_path, script)
    dialog = MapSettingsDialog(model)
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog, model, keys, cache


def test_the_dialog_starts_without_a_key_and_hides_what_is_typed(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, _keys, _cache = make_dialog(qtbot, tmp_path)

    assert dialog.key_status_text == "No key is saved."
    assert dialog.key_field.echoMode() == QLineEdit.EchoMode.Password
    assert not dialog.remove_button.isEnabled()
    assert dialog.remove_button.toolTip() == "There is no key to remove."
    assert dialog.cache_field.text() in {"4"}
    model.close()


def test_saving_a_good_key_says_so_clears_the_field_and_tells_the_window(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, keys, _cache = make_dialog(qtbot, tmp_path)
    dialog.key_field.setText(GOOD)

    with qtbot.waitSignal(dialog.key_changed, timeout=WAIT_MS):
        dialog.save_button.click()
    assert dialog.key_message_text  # "Checking..." or already the result

    qtbot.waitUntil(lambda: dialog.key_message_text.startswith("Key saved."), timeout=WAIT_MS)
    assert dialog.key_field.text() == ""  # the key does not stay on screen
    assert dialog.key_status_text == "A key is saved."
    assert dialog.remove_button.isEnabled()
    assert keys.key == TileKey(GOOD)
    assert GOOD not in dialog.key_message_text
    model.close()


def test_a_refused_key_gets_a_message_with_the_remedy_and_the_field_keeps_the_text(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, keys, _cache = make_dialog(qtbot, tmp_path)
    dialog.key_field.setText(OTHER_VALID)

    dialog.save_button.click()

    qtbot.waitUntil(lambda: "refused" in dialog.key_message_text, timeout=WAIT_MS)
    assert "Copy it again" in dialog.key_message_text
    assert keys.key is None
    assert dialog.key_field.text() == OTHER_VALID  # so it can be corrected
    assert dialog.save_button.isEnabled()
    assert OTHER_VALID not in dialog.key_message_text
    model.close()


def test_removing_the_key_from_the_dialog(qtbot: QtBot, tmp_path: Path) -> None:
    dialog, model, keys, _cache = make_dialog(qtbot, tmp_path)
    keys.save(TileKey(GOOD))
    dialog.refresh()
    assert dialog.remove_button.isEnabled()

    with qtbot.waitSignal(dialog.key_changed, timeout=WAIT_MS):
        dialog.remove_button.click()

    assert keys.key is None
    assert dialog.key_status_text == "No key is saved."
    assert dialog.key_message_text == "Key removed. The map shows a plain background."
    model.close()


def test_the_cache_size_applies_when_the_field_is_left_and_a_wrong_one_is_explained(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, _keys, _cache = make_dialog(qtbot, tmp_path)

    dialog.cache_field.setText("0,5")
    dialog.cache_field.editingFinished.emit()
    assert model.cache_limit().bytes == GIB // 2

    dialog.cache_field.setText("lots")
    dialog.cache_field.editingFinished.emit()
    assert "Enter a size in GB between" in dialog.cache_message_text
    assert model.cache_limit().bytes == GIB // 2  # unchanged
    model.close()


def push_tile(cache: InMemoryTileCache) -> int:
    cache.put(
        "en", TileAddress(4, 2, 2), TileImage(make_png(extra_bytes=50_000), 3600), datetime.now(UTC)
    )
    return cache.used_bytes()


def answer_clear(dialog: MapSettingsDialog, button_text: str) -> QMessageBox:
    box = dialog.open_boxes[0]
    next(b for b in box.buttons() if b.text() == button_text).click()
    return box


def test_clear_cache_asks_first_says_how_much_it_frees_and_the_button_names_the_action(
    qtbot: QtBot, tmp_path: Path
) -> None:
    dialog, model, _keys, cache = make_dialog(qtbot, tmp_path)
    used = push_tile(cache)

    dialog.clear_button.click()

    qtbot.waitUntil(lambda: bool(dialog.open_boxes), timeout=WAIT_MS)
    box = dialog.open_boxes[0]
    assert "frees" in box.text()
    assert "Tracestrack credit" in box.informativeText()  # the cost of clearing, said up front
    labels = sorted(b.text() for b in box.buttons())
    assert labels == ["Clear cache", "Keep cache"]  # verbs, never OK and Cancel
    assert box.defaultButton().text() == "Keep cache"
    assert cache.used_bytes() == used  # asking changed nothing
    model.close()


def test_keeping_the_cache_leaves_it_and_clearing_empties_it(qtbot: QtBot, tmp_path: Path) -> None:
    dialog, model, _keys, cache = make_dialog(qtbot, tmp_path)
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
    model.close()
