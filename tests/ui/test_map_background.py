# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The choice of the map background: the Map menu, the control on the map, the page and the
remembered choice (the web engine is real, the tiles are fakes)."""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.maps.domain import (
    BACKGROUNDS,
    CYCLOSM,
    DEFAULT_BACKGROUND,
    OPENSTREETMAP,
    OPENTOPOMAP,
    MapBackground,
)
from ponos_coach.maps.ports import TileProblem, TileUnavailable
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.map_background import KEY_BACKGROUND, BackgroundChoice, saved_background
from ponos_coach.ui.map_messages import background_message, page_call, parse_background_key
from ponos_coach.ui.map_web_view import MapWebView
from ponos_coach.ui.web_policy import tile_url_template
from tests.fakes.tiles import FakeTileSource
from tests.ui.map_support import FAR, NEAR, show_view
from tests.ui.test_chart_view import evaluate, execute
from tests.ui.test_map_page_web import page_state, window_with_page
from tests.ui.workspace_support import WAIT_MS, TileFakes, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def shown(qtbot: QtBot, view: MapWebView, background: MapBackground) -> bool:
    """Whether the page shows this background (tile URL, credit and highest zoom)."""
    state = page_state(qtbot, view)
    return (
        state["background"] == background.key
        and state["tileUrl"] == tile_url_template(background)
        and state["attribution"] == background.attribution
        and state["maxZoom"] == background.max_zoom
    )


def checked_in_menu(window: MainWindow) -> list[str]:
    return [key for key, a in window.map_ui.background_actions.items() if a.isChecked()]


# --- The remembered choice (no window) ------------------------------------------------------------


def test_the_default_background_is_openstreetmap_and_the_choice_is_remembered(
    settings: QSettings,
) -> None:
    choice = BackgroundChoice(settings)
    changes: list[object] = []
    choice.changed.connect(changes.append)

    assert choice.current is DEFAULT_BACKGROUND is OPENSTREETMAP
    choice.choose(OPENSTREETMAP)  # already in use: nothing
    choice.choose(CYCLOSM)

    assert changes == [CYCLOSM]
    assert settings.value(KEY_BACKGROUND) == "cyclosm"
    assert BackgroundChoice(settings).current is CYCLOSM  # the next start


@pytest.mark.parametrize(
    "raw", ["", "evil", "https://example.org/{z}/{x}/{y}.png", "OpenTopoMap", 7]
)
def test_a_saved_value_that_is_not_a_known_key_is_the_default(
    settings: QSettings, raw: object
) -> None:
    settings.setValue(KEY_BACKGROUND, raw)

    assert saved_background(settings) is OPENSTREETMAP


@pytest.mark.parametrize("background", BACKGROUNDS, ids=lambda b: b.key)
def test_the_page_message_carries_the_key_the_url_the_zoom_and_the_credit(
    background: MapBackground,
) -> None:
    message = background_message(background, tile_url_template(background))

    assert message == {
        "key": background.key,
        "tileUrl": f"ponos://tiles/{background.key}/{{z}}/{{x}}/{{y}}",
        "maxZoom": background.max_zoom,
        "attribution": background.attribution,
    }
    assert page_call("setBackground", message).startswith("ponosMap.setBackground(")


@pytest.mark.parametrize("bad", ["", "evil", None, 3, "cyclosm ", "CYCLOSM"])
def test_the_page_can_only_name_a_background_of_the_closed_set(bad: object) -> None:
    assert parse_background_key(bad) is None
    assert parse_background_key("opentopomap") is OPENTOPOMAP


# --- Menu, page and control stay together ---------------------------------------------------------


def test_the_map_menu_has_three_exclusive_backgrounds_and_openstreetmap_is_checked(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)

    actions = window.map_ui.background_actions
    assert [a.text() for a in actions.values()] == ["OpenStreetMap", "OpenTopoMap", "CyclOSM"]
    assert [a.shortcut().toString() for a in actions.values()] == [
        "Ctrl+Shift+1",
        "Ctrl+Shift+2",
        "Ctrl+Shift+3",
    ]
    assert checked_in_menu(window) == ["openstreetmap"]
    assert shown(qtbot, view, OPENSTREETMAP)


def test_choosing_a_background_in_the_menu_changes_the_page_without_reloading_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    execute(qtbot, view, "window.__same_page = 1;")  # a reload would lose it

    window.map_ui.background_actions["opentopomap"].trigger()
    qtbot.waitUntil(lambda: shown(qtbot, view, OPENTOPOMAP), timeout=WAIT_MS)
    window.map_ui.background_actions["cyclosm"].trigger()
    qtbot.waitUntil(lambda: shown(qtbot, view, CYCLOSM), timeout=WAIT_MS)

    assert evaluate(qtbot, view, "window.__same_page") == 1
    assert checked_in_menu(window) == ["cyclosm"]
    assert settings.value(KEY_BACKGROUND) == "cyclosm"
    states = page_state(qtbot, view)["backgroundButtons"]
    assert states == [
        {"key": "openstreetmap", "pressed": False},
        {"key": "opentopomap", "pressed": False},
        {"key": "cyclosm", "pressed": True},
    ]


def test_a_zoom_above_the_maximum_of_the_new_background_comes_down_to_it(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    window.map_ui.background_actions["cyclosm"].trigger()
    qtbot.waitUntil(lambda: shown(qtbot, view, CYCLOSM), timeout=WAIT_MS)
    execute(qtbot, view, "window.ponosMap.setView({lat: 0, lon: 0, zoom: 20});")
    qtbot.waitUntil(lambda: page_state(qtbot, view)["zoom"] == 20, timeout=WAIT_MS)

    window.map_ui.background_actions["opentopomap"].trigger()

    qtbot.waitUntil(lambda: shown(qtbot, view, OPENTOPOMAP), timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: page_state(qtbot, view)["zoom"] == OPENTOPOMAP.max_zoom, timeout=WAIT_MS
    )


def test_the_control_on_the_map_chooses_the_background_and_the_menu_follows(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)

    execute(
        qtbot,
        view,
        "document.querySelector('.ponos-backgrounds button[data-key=opentopomap]').click();",
    )

    qtbot.waitUntil(lambda: window.map_ui.background.current is OPENTOPOMAP, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: shown(qtbot, view, OPENTOPOMAP), timeout=WAIT_MS)
    assert checked_in_menu(window) == ["opentopomap"]
    assert settings.value(KEY_BACKGROUND) == "opentopomap"


def test_the_page_cannot_make_up_a_background(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    rejected = view.bridge.rejected

    execute(
        qtbot, view, 'window.__bridge.backgroundChosen("https://evil.example/{z}/{x}/{y}.png");'
    )

    qtbot.waitUntil(lambda: view.bridge.rejected > rejected, timeout=WAIT_MS)
    assert window.map_ui.background.current is OPENSTREETMAP
    assert shown(qtbot, view, OPENSTREETMAP)


def test_a_new_window_starts_with_the_background_chosen_before(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    settings.setValue(KEY_BACKGROUND, "opentopomap")

    window, view = window_with_page(qtbot, tmp_path, settings)

    assert window.map_ui.background.current is OPENTOPOMAP
    assert checked_in_menu(window) == ["opentopomap"]
    assert shown(qtbot, view, OPENTOPOMAP)


def test_the_credit_of_the_background_in_use_is_always_visible(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)

    for background in (OPENTOPOMAP, CYCLOSM, OPENSTREETMAP):
        window.map_ui.background_actions[background.key].trigger()
        qtbot.waitUntil(partial(shown, qtbot, view, background), timeout=WAIT_MS)
        # only the credit of the background in use, never two at once
        assert page_state(qtbot, view)["attributionVisible"] is True


def test_the_notice_of_a_problem_names_the_background_it_is_about(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    tiles = TileFakes(FakeTileSource([TileUnavailable(TileProblem.REFUSED)]))
    window, _view = window_with_page(qtbot, tmp_path, settings, tiles)
    show_view(qtbot, window, NEAR)
    page = window.map_page
    qtbot.waitUntil(lambda: page.notice_visible, timeout=WAIT_MS)
    assert page.notice_text.startswith("OpenStreetMap: ")

    window.map_ui.background_actions["cyclosm"].trigger()

    # The refusal was OpenStreetMap's: the notice goes with it; CyclOSM is asked and refuses too.
    show_view(qtbot, window, FAR)
    qtbot.waitUntil(
        lambda: page.notice_visible and page.notice_text.startswith("CyclOSM: "), timeout=WAIT_MS
    )
