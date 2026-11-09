# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map of the activity-detail window: the track beside the summary, in the real web engine.

The data are made-up positions (a short path in open sea); the tiles come from the fake provider
through the tile proxy, never from the network (T-SEC-15 for this page).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PySide6.QtCore import QPoint, QUrl
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot
from shiboken6 import isValid

from ponos_coach.load.activity_detail import ActivityDetail, TrackOutline
from ponos_coach.load.domain import ZoneSeconds
from ponos_coach.maps.domain import TileKey
from ponos_coach.maps.use_cases import TileProxy
from ponos_coach.ui.activity_detail import DetailReady
from ponos_coach.ui.activity_detail_dialog import ActivityDetailDialog
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.tile_service import TileService
from ponos_coach.ui.tokens import Theme, tokens_for
from ponos_coach.ui.track_view import TrackStrings, track_script
from ponos_coach.ui.web_policy import TILE_URL_TEMPLATE, is_request_allowed
from ponos_coach.ui.web_resources import resource_for_path
from tests.fakes.tiles import FakeTileSource, InMemoryTileCache, InMemoryTileKeyStore
from tests.ui.test_activity_detail import detail_with
from tests.ui.test_chart_view import evaluate, execute
from tests.ui.workspace_support import WAIT_MS

STRINGS = TrackStrings("Map of the activity", "Zoom in", "Zoom out", "Start", "Finish")
LAT = 40.0 + np.arange(40, dtype=np.float64) * 0.0005  # a made-up path at sea
LON = 5.0 + np.arange(40, dtype=np.float64) * 0.0007
_CALL = "ponosTrack.show("


def outline(*, gap_at: int | None = None) -> TrackOutline:
    segment = np.zeros(40, dtype=np.intp)
    if gap_at is not None:
        segment[gap_at:] = 1
    return TrackOutline(LAT.copy(), LON.copy(), segment)


def detail_with_track(track: TrackOutline | None) -> ActivityDetail:
    base = detail_with(ZoneSeconds((300.0, 100.0, 0.0, 0.0, 100.0)))
    return dataclasses.replace(base, outline=track)


class Rig:
    """An engine with the tile service on the fake provider, and the dialogs that use it."""

    def __init__(self, keys: InMemoryTileKeyStore) -> None:
        self.source = FakeTileSource(keys)
        proxy = TileProxy(self.source, InMemoryTileCache(), lambda: datetime.now(UTC), lambda: 0.0)
        self.tiles = TileService(proxy, lambda: "en")
        self.engine = ChartEngine(lambda _name: None, tiles=self.tiles)

    def dialog(self, qtbot: QtBot, track: TrackOutline | None) -> ActivityDetailDialog:
        dialog = ActivityDetailDialog(
            self.engine, ZoneInfo("Europe/Rome"), theme=lambda: Theme.LIGHT
        )
        qtbot.addWidget(dialog)
        dialog.show()
        qtbot.waitExposed(dialog)
        dialog.show_outcome(DetailReady(detail_with_track(track), None))
        return dialog


@pytest.fixture
def rig() -> Iterator[Rig]:
    made = Rig(InMemoryTileKeyStore(TileKey(FakeTileSource.ACCEPTED_KEY)))
    yield made
    made.engine.dispose()


@pytest.fixture
def keyless_rig() -> Iterator[Rig]:
    made = Rig(InMemoryTileKeyStore())
    yield made
    made.engine.dispose()


def page_state(qtbot: QtBot, dialog: ActivityDetailDialog) -> dict[str, object]:
    state = evaluate(qtbot, dialog.track.view, "window.ponosTrack.state()")
    assert isinstance(state, dict)
    return state


def wait_drawn(qtbot: QtBot, dialog: ActivityDetailDialog) -> dict[str, object]:
    def drawn() -> bool:
        box: list[object] = []
        dialog.track.view.page.runJavaScript(
            "typeof ponosTrack !== 'undefined' && ponosTrack.state().drawn !== null", box.append
        )
        qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)
        return box[0] is True

    qtbot.waitUntil(drawn, timeout=WAIT_MS)
    return page_state(qtbot, dialog)


# --- What is sent to the page (no window) -----------------------------------------------------


def test_the_script_carries_the_points_the_colours_of_the_theme_and_no_key() -> None:
    light, dark = tokens_for(Theme.LIGHT), tokens_for(Theme.DARK)

    script = track_script(outline(gap_at=20), light, STRINGS)

    assert script.startswith(_CALL)
    assert script.endswith(");")
    payload = json.loads(script[len(_CALL) : -2])
    assert len(payload["lat"]) == len(payload["lon"]) == len(payload["segment"]) == 40
    assert payload["lat"][0] == pytest.approx(40.0)
    assert payload["segment"][19:21] == [0, 1]  # the gap is told to the page
    assert payload["tileUrl"] == TILE_URL_TEMPLATE
    assert payload["colors"]["track"] == light.categories[0]
    assert payload["colors"]["background"] == light.map_background
    dark_payload = json.loads(track_script(outline(), dark, STRINGS)[len(_CALL) : -2])
    assert dark_payload["colors"]["background"] == dark.map_background
    assert FakeTileSource.ACCEPTED_KEY not in script


def test_the_page_is_in_the_closed_list_with_a_policy_that_allows_only_the_app_scheme() -> None:
    html = resource_for_path("/track.html")
    script = resource_for_path("/track.js")

    assert html is not None
    assert script is not None
    text = html.content.decode("utf-8")
    assert "connect-src 'none'" in text
    assert "default-src 'none'" in text
    assert "http:" not in text
    assert "https:" not in text
    assert "qwebchannel" not in text  # a page that only draws has no channel
    for forbidden in (b"http://", b"https://", b"WebSocket", b"fetch(", b"XMLHttpRequest"):
        assert forbidden not in script.content


# --- Layout and states ------------------------------------------------------------------------


def test_with_gps_the_map_sits_at_the_right_of_the_summary(qtbot: QtBot, rig: Rig) -> None:
    dialog = rig.dialog(qtbot, outline())

    assert dialog.shows_track()
    assert dialog.no_gps_text == ""
    summary = dialog.findChild(QLabel, "detail-start")
    assert summary is not None
    summary_right = summary.mapTo(dialog, QPoint(summary.width(), 0)).x()
    track_left = dialog.track.mapTo(dialog, QPoint(0, 0)).x()
    assert track_left >= summary_right  # the map starts where the summary ends
    track_top = dialog.track.mapTo(dialog, QPoint(0, 0)).y()
    chart_top = dialog.chart.mapTo(dialog, QPoint(0, 0)).y()
    assert track_top < chart_top  # and both are above the chart


def test_without_gps_a_short_message_takes_the_place_of_the_map(qtbot: QtBot, rig: Rig) -> None:
    dialog = rig.dialog(qtbot, None)

    assert not dialog.shows_track()
    assert dialog.no_gps_text == "No GPS track in this activity."
    assert not dialog.track.view.engine_started  # no page for an indoor activity


# --- The page ---------------------------------------------------------------------------------


def test_the_page_receives_the_track_and_frames_it_with_start_and_finish_marked(
    qtbot: QtBot, rig: Rig
) -> None:
    dialog = rig.dialog(qtbot, outline(gap_at=20))

    state = wait_drawn(qtbot, dialog)

    drawn = state["drawn"]
    assert isinstance(drawn, dict)
    assert drawn["points"] == 40
    assert drawn["stretches"] == 2
    assert drawn["trackColor"] == tokens_for(Theme.LIGHT).categories[0]
    assert state["pins"] == 2  # start (round) and finish (square), told apart without colour
    bounds = state["bounds"]
    assert isinstance(bounds, list)
    south, west, north, east = bounds
    assert south <= LAT.min()
    assert north >= LAT.max()
    assert west <= LON.min()
    assert east >= LON.max()


def test_the_page_asks_for_tiles_only_through_the_proxy_and_never_holds_the_key(
    qtbot: QtBot, rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    dialog = rig.dialog(qtbot, outline())

    state = wait_drawn(qtbot, dialog)

    assert state["tileUrl"] == TILE_URL_TEMPLATE
    qtbot.waitUntil(lambda: len(rig.source.calls) > 0, timeout=WAIT_MS)
    html = evaluate(qtbot, dialog.track.view, "document.documentElement.outerHTML")
    assert isinstance(html, str)
    assert FakeTileSource.ACCEPTED_KEY not in html
    assert not [r for r in caplog.records if "Blocked web request" in r.getMessage()]
    assert not [r for r in caplog.records if FakeTileSource.ACCEPTED_KEY in r.getMessage()]


def test_the_page_cannot_reach_the_network_or_other_hosts(
    qtbot: QtBot, rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = rig.dialog(qtbot, outline())
    wait_drawn(qtbot, dialog)
    caplog.set_level(logging.WARNING)
    execute(
        qtbot,
        dialog.track.view,
        'window.__done = 0; for (const u of ["http://127.0.0.1:9/x.png", "https://example.com/y.png",'
        ' "file:///C:/Windows/win.ini", "ponos://evil/z.png", "ponos://tiles/99/0/0"]) {'
        " const i = new Image(); i.onerror = () => { window.__done += 1; }; i.src = u; }",
    )

    qtbot.waitUntil(
        lambda: evaluate(qtbot, dialog.track.view, "window.__done") == 5, timeout=WAIT_MS
    )

    assert [r for r in caplog.records if "Blocked web request" in r.getMessage()]
    assert (99, 0, 0, "en") not in rig.source.calls
    assert is_request_allowed(QUrl("ponos://app/track.js"))
    assert not is_request_allowed(QUrl("ponos://tiles/99/0/0"))


def test_without_a_key_the_track_stays_on_a_plain_background_with_one_line_of_text(
    qtbot: QtBot, keyless_rig: Rig
) -> None:
    dialog = keyless_rig.dialog(qtbot, outline())
    wait_drawn(qtbot, dialog)

    qtbot.waitUntil(lambda: dialog.track.tile_problem_text != "", timeout=WAIT_MS)

    assert dialog.track.tile_problem_text == "The map background needs a Tracestrack key."
    assert dialog.shows_track()  # the track is still there


# --- Closing ----------------------------------------------------------------------------------


def test_closing_the_window_frees_the_page_of_the_map(qtbot: QtBot, rig: Rig) -> None:
    dialog = rig.dialog(qtbot, outline())
    wait_drawn(qtbot, dialog)
    view = dialog.track.view
    page = view.page

    dialog.close()

    assert not isValid(page)
    assert not view.engine_started
    view.start_engine()  # closed: no new page
    assert not view.engine_started
