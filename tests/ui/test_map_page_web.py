# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-MAP-01, T-MAP-08, T-MAP-09, T-MAP-10, T-SEC-14, T-SEC-15: the map page in the real web engine.

These tests start Chromium (QtWebEngine) with the page served by the app's scheme, Leaflet from
the packaged files and tiles from the fake provider through the tile proxy. The page is told what
to show by the application and its answers are read through the diagnostics hooks of `map.js`.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl
from pytestqt.qtbot import QtBot

from ponos_coach.load.activity_profile import ActivityProfileData
from ponos_coach.maps.domain import TileKey
from ponos_coach.maps.ports import TileProblem, TileUnavailable
from ponos_coach.ui.chart_view import ChartView
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.map_messages import page_call
from ponos_coach.ui.map_web_view import MapWebView
from ponos_coach.ui.web_policy import is_request_allowed
from tests.fakes.tiles import FakeTileSource, InMemoryTileKeyStore
from tests.ui.map_support import NEAR, map_window, show_view
from tests.ui.test_chart_view import evaluate, execute
from tests.ui.workspace_support import WAIT_MS, TileFakes, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def wait_page(qtbot: QtBot, view: MapWebView) -> None:
    """Wait until Leaflet has built the map in the page."""

    def built() -> bool:
        box: list[object] = []
        view.page.runJavaScript("document.querySelector('.leaflet-container') !== null", box.append)
        qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)
        return box[0] is True

    qtbot.waitUntil(built, timeout=WAIT_MS)


def page_state(qtbot: QtBot, view: MapWebView) -> dict[str, object]:
    state = evaluate(qtbot, view, "window.ponosMap.state()")
    assert isinstance(state, dict)
    return state


def group_count(qtbot: QtBot, view: MapWebView) -> int:
    groups = evaluate(qtbot, view, "window.ponosMap.visibleGroups()")
    assert isinstance(groups, list)
    return len(groups)


def at_zoom(qtbot: QtBot, view: MapWebView, zoom: int) -> Callable[[], bool]:
    return lambda: page_state(qtbot, view)["zoom"] == zoom


def wait_chart(qtbot: QtBot, chart: ChartView) -> None:
    """Wait until Plotly has drawn the elevation profile."""

    def drawn() -> bool:
        box: list[object] = []
        chart.page.runJavaScript(
            "typeof Plotly !== 'undefined' && document.getElementById('chart') !== null"
            " && document.getElementById('chart').data !== undefined",
            box.append,
        )
        qtbot.waitUntil(lambda: bool(box), timeout=WAIT_MS)
        return box[0] is True

    qtbot.waitUntil(drawn, timeout=WAIT_MS)


def window_with_page(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, tiles: TileFakes | None = None
) -> tuple[MainWindow, MapWebView]:
    window = map_window(qtbot, tmp_path, settings, tiles)
    window.show()
    window.map_page.resize(1000, 700)
    view = window.map_page.map_view
    wait_page(qtbot, view)
    # Otherwise the page's first report (the whole world) can arrive after the test's own view.
    wait_until_the_page_reported_its_view(qtbot, view)
    return window, view


def wait_until_the_page_reported_its_view(qtbot: QtBot, view: MapWebView) -> None:
    """Wait until the page's own view report reached Python.

    The page reports its view by itself once its channel opens, after a debounce. Once nothing is
    pending, one rejected message sent on the same channel arrives after any report already sent:
    the channel keeps the order of the messages. The test's own channel is `window.__bridge`.
    """
    execute(
        qtbot,
        view,
        "if (window.__bridge === undefined) { new QWebChannel(qt.webChannelTransport,"
        " function (c) { window.__bridge = c.objects.bridge; }); }",
    )
    qtbot.waitUntil(
        lambda: evaluate(qtbot, view, "window.__bridge !== undefined") is True, timeout=WAIT_MS
    )

    def settled() -> bool:
        state = page_state(qtbot, view)
        return state["channelOpen"] is True and state["viewPending"] is False

    qtbot.waitUntil(settled, timeout=WAIT_MS)
    rejected = view.bridge.rejected
    execute(qtbot, view, "window.__bridge.pointHovered(-1, 0);")
    qtbot.waitUntil(lambda: view.bridge.rejected > rejected, timeout=WAIT_MS)


def keyed_tiles() -> TileFakes:
    keys = InMemoryTileKeyStore()
    keys.save(TileKey(FakeTileSource.ACCEPTED_KEY))
    return TileFakes(keys, FakeTileSource(keys))


# --- The page and the network (T-SEC-15) ----------------------------------------------------


def test_the_page_asks_for_tiles_only_through_the_app_scheme_and_the_proxy(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    tiles = keyed_tiles()
    caplog.set_level(logging.WARNING)

    _window, view = window_with_page(qtbot, tmp_path, settings, tiles)

    qtbot.waitUntil(lambda: len(tiles.source.calls) > 0, timeout=WAIT_MS)
    assert all(0 <= z <= 19 for z, _x, _y, _style in tiles.source.calls)
    assert {style for *_rest, style in tiles.source.calls} == {"en"}  # the app's language
    assert page_state(qtbot, view)["tileUrl"] == "ponos://tiles/{z}/{x}/{y}"
    assert not [r for r in caplog.records if "Blocked web request" in r.getMessage()]


def test_the_page_cannot_reach_the_network_or_other_hosts(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    tiles = keyed_tiles()
    _window, view = window_with_page(qtbot, tmp_path, settings, tiles)
    caplog.set_level(logging.WARNING)
    execute(
        qtbot,
        view,
        'window.__done = 0; for (const u of ["http://127.0.0.1:9/x.png", "https://example.com/y.png",'
        ' "file:///C:/Windows/win.ini", "ponos://evil/z.png", "ponos://tiles/99/0/0"]) {'
        " const i = new Image(); i.onerror = () => { window.__done += 1; }; i.src = u; }",
    )

    qtbot.waitUntil(lambda: evaluate(qtbot, view, "window.__done") == 5, timeout=WAIT_MS)

    # Every one of them failed (the page's policy stops http, https, file and foreign hosts before
    # the network; the interceptor stops what looks like a tile but is not one).
    blocked = [r for r in caplog.records if "Blocked web request" in r.getMessage()]
    assert blocked
    assert (99, 0, 0, "en") not in tiles.source.calls
    for url in (
        "ponos://tiles/3/4/5",
        "ponos://app/map.js",
    ):
        assert is_request_allowed(QUrl(url))
    for url in ("ponos://tiles/99/0/0", "ponos://tiles/3/4/5?x=1", "ponos://tiles/3/4/5/6"):
        assert not is_request_allowed(QUrl(url))


def test_the_map_page_declares_a_policy_without_hosts_and_the_attribution_is_visible(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, view = window_with_page(qtbot, tmp_path, settings)

    html = evaluate(qtbot, view, "document.querySelector('meta[http-equiv]').content")

    assert isinstance(html, str)
    assert "connect-src 'none'" in html
    assert "default-src 'none'" in html
    assert "http" not in html
    assert "ponos://tiles" in html
    state = page_state(qtbot, view)
    assert state["attributionVisible"] is True
    assert "OpenStreetMap" in str(state["attribution"])
    assert "Tracestrack" in str(state["attribution"])


# --- Groups (T-MAP-01) ----------------------------------------------------------------------


def test_the_numbers_of_the_visible_groups_add_up_to_the_starts_at_every_zoom(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, view = window_with_page(qtbot, tmp_path, settings)
    rng = random.Random(7)
    points = [(40.0 + rng.random(), 5.0 + rng.random()) for _ in range(300)]  # invented, at sea
    data: list[float | int] = []
    for index, (lat, lon) in enumerate(points):
        data += [round(lat, 5), round(lon, 5), index, 0]
    view.call("setStarts", {"data": data})

    for zoom in (3, 5, 7, 8):
        view.call("setView", {"lat": 40.5, "lon": 5.5, "zoom": zoom})
        qtbot.waitUntil(at_zoom(qtbot, view, zoom), timeout=WAIT_MS)
        groups = evaluate(qtbot, view, "window.ponosMap.visibleGroups()")
        assert isinstance(groups, list)
        assert sum(group["count"] for group in groups) == len(points)

    # Groups merge when zooming out and split when zooming in (after the animation, if any).
    view.call("setView", {"lat": 40.5, "lon": 5.5, "zoom": 3})
    qtbot.waitUntil(lambda: group_count(qtbot, view) <= 3, timeout=WAIT_MS)
    view.call("setView", {"lat": 40.5, "lon": 5.5, "zoom": 8})
    qtbot.waitUntil(lambda: group_count(qtbot, view) >= 10, timeout=WAIT_MS)


# --- The view reported by the page ----------------------------------------------------------


def test_moving_the_map_makes_the_page_report_its_view_to_the_controller(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)

    with qtbot.waitSignal(view.bridge.view_changed, timeout=WAIT_MS, raising=True) as blocker:
        view.call("setView", {"lat": -30.0, "lon": -20.0, "zoom": 12})

    reported = blocker.args[0]
    assert reported.zoom == 12
    assert reported.box.south < -30.0 < reported.box.north
    assert window.map_ui.controller.view is not None


# --- Tracks, selection and the linked cursor (T-MAP-08, T-MAP-09) ---------------------------


def select_first(qtbot: QtBot, window: MainWindow) -> ActivityProfileData:
    profiles: list[object] = []
    window.map_ui.controller.profile_changed.connect(profiles.append)
    window.map_ui.controller.select_index(0)
    qtbot.waitUntil(
        lambda: any(isinstance(p, ActivityProfileData) for p in profiles), timeout=WAIT_MS
    )
    return next(p for p in profiles if isinstance(p, ActivityProfileData))


def test_tracks_are_drawn_from_zoom_11_with_the_selected_one_highlighted(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)

    qtbot.waitUntil(lambda: page_state(qtbot, view)["tracks"] == 8, timeout=WAIT_MS)
    assert page_state(qtbot, view)["starts"] == 8
    window.map_ui.controller.select_index(2)
    qtbot.waitUntil(lambda: page_state(qtbot, view)["selected"] == 2, timeout=WAIT_MS)
    assert page_state(qtbot, view)["selectedColors"]  # casing, line and ring of the selection


def test_the_cursor_goes_from_the_profile_to_the_map_and_back(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    profile = select_first(qtbot, window)
    page = window.map_page
    chart = page.chart
    qtbot.waitUntil(lambda: page.shows_chart and chart.engine_started, timeout=WAIT_MS)
    wait_chart(qtbot, chart)
    index = len(profile) // 2

    # Profile to map: hovering a point of the chart (Plotly's own event) puts the cursor there.
    # (Plotly's own event, emitted on the graph div: a synthetic pointer is not reliable offscreen.)
    execute(
        qtbot,
        chart,
        "document.getElementById('chart').emit('plotly_hover',"
        f" {{points: [{{curveNumber: 0, pointIndex: {index}}}]}});",
    )
    qtbot.waitUntil(lambda: page_state(qtbot, view)["cursor"] is not None, timeout=WAIT_MS)
    cursor = page_state(qtbot, view)["cursor"]
    assert cursor == pytest.approx([profile.lat_deg[index], profile.lon_deg[index]], abs=1e-5)

    # Map to profile: the pointer over the track puts the chart's cursor at that distance.
    spot = evaluate(qtbot, view, f"window.ponosMap.pixelOf({{index: {index + 3}}})")
    assert isinstance(spot, dict)
    with qtbot.waitSignal(view.bridge.point_hovered, timeout=WAIT_MS) as blocker:
        execute(
            qtbot,
            view,
            "document.getElementById('map').dispatchEvent(new MouseEvent('mousemove', "
            f"{{clientX: {spot['x']}, clientY: {spot['y']}, bubbles: true}}));",
        )
    hovered = blocker.args[0]
    # At this zoom a sample is well under a pixel apart: the pointer picks one of the neighbours.
    assert abs(hovered - (index + 3)) <= 40
    expected_km = float(profile.distance_m[hovered]) / 1000.0
    shapes = evaluate(qtbot, chart, "document.getElementById('chart').layout.shapes")
    assert isinstance(shapes, list)
    assert shapes[0]["x0"] == pytest.approx(expected_km)


def test_the_track_is_drawn_by_heart_rate_zone_with_the_stretches_without_hr_grey_and_dashed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    page = window.map_page
    page.color_mode.setCurrentIndex(1)  # by heart-rate zone
    profile = select_first(qtbot, window)
    if profile.zone is None:
        pytest.skip("the made-up profile has no heart-rate limits: nothing to colour")
    qtbot.waitUntil(lambda: page.effective_mode == "zones", timeout=WAIT_MS)

    qtbot.waitUntil(lambda: bool(page_state(qtbot, view)["zoneColors"]), timeout=WAIT_MS)

    colors = page_state(qtbot, view)["zoneColors"]
    assert isinstance(colors, list)
    assert len(page.zone_legend.entries) == 6  # five zones and "no usable heart rate"
    assert all(entry.text for entry in page.zone_legend.entries)
    zone_colors = {entry.color for entry in page.zone_legend.entries}
    assert set(colors) <= zone_colors
    # Same zone for the same stretch as the profile columns say: one run per change of zone.
    runs = 1 + sum(1 for a, b in zip(profile.zone[:-2], profile.zone[1:-1], strict=True) if a != b)
    assert len(colors) == runs


# --- Messages from the page (T-SEC-14) ------------------------------------------------------


def test_the_page_can_reach_one_object_with_five_slots_and_nothing_else(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    _window, view = window_with_page(qtbot, tmp_path, settings)
    execute(
        qtbot,
        view,
        "new QWebChannel(qt.webChannelTransport, function (c) {"
        " window.__objects = Object.keys(c.objects);"
        " window.__methods = Object.keys(c.objects.bridge).filter("
        "   k => typeof c.objects.bridge[k] === 'function').sort();"
        " window.__bridge = c.objects.bridge; });",
    )
    qtbot.waitUntil(
        lambda: evaluate(qtbot, view, "window.__objects") == ["bridge"], timeout=WAIT_MS
    )

    methods = evaluate(qtbot, view, "window.__methods")

    assert isinstance(methods, list)
    slots = {
        "activityClicked",
        "pageReady",
        "pointHovered",
        "pointLeft",
        "viewChanged",
    }
    # QWebChannel lists each slot twice (by name and with its signature), adds its own plumbing and
    # publishes QObject's `deleteLater`: nothing else can be called.
    named = {m for m in methods if "(" not in m}
    plumbing = {"propertyUpdate", "signalEmitted", "unwrapProperties", "unwrapQObject"}
    assert named - plumbing == slots | {"deleteLater"}


@pytest.mark.parametrize(
    "script",
    [
        "b.viewChanged(0, 0, 1, 1, 99)",
        "b.viewChanged(0, 0, 1, 1, -3)",
        "b.viewChanged(0, 1e308, 1, 1e308, 5)",
        "b.viewChanged(0, 10, 1, 5, 5)",
        "b.activityClicked(-1, GEN)",
        "b.activityClicked(1e12, GEN)",
        "b.pointHovered(5, 0)",
        "b.pointHovered(-1, 0)",
    ],
)
def test_malformed_messages_from_the_page_are_rejected_without_effect(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, script: str
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)  # eight activities in the list, no profile: no point to hover
    wait_until_the_page_reported_its_view(qtbot, view)  # showing NEAR moved the page again
    controller = window.map_ui.controller
    before_view, before_selected = controller.view, controller.selected
    rejected_before = view.bridge.rejected
    seen: list[object] = []
    view.bridge.view_changed.connect(seen.append)
    view.bridge.activity_clicked.connect(seen.append)
    view.bridge.point_hovered.connect(seen.append)

    script = script.replace("GEN", str(view.bridge.activity_generation))
    execute(qtbot, view, f"(function (b) {{ {script}; }})(window.__bridge);")
    qtbot.waitUntil(lambda: view.bridge.rejected > rejected_before, timeout=WAIT_MS)

    assert seen == []
    assert controller.view == before_view
    assert controller.selected == before_selected


def test_a_valid_click_message_selects_the_activity_it_names(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, view = window_with_page(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)

    view.bridge.activity_clicked.emit(4)  # what the page emits after validation

    search = window.map_ui.controller.search
    assert search is not None
    assert window.map_ui.controller.selected == search.activities[4].identity


def test_the_page_functions_are_a_closed_set_and_the_payload_is_json() -> None:
    assert page_call("fitAll", {}) == "ponosMap.fitAll({});"
    assert json.loads(page_call("setCursor", {"index": None})[len("ponosMap.setCursor(") : -2]) == {
        "index": None
    }
    for name in ("alert", "eval", "constructor", "init; alert(1); //", ""):
        with pytest.raises(ValueError):
            page_call(name, {})
    with pytest.raises(ValueError):  # NaN has no JSON form: it never reaches the page
        page_call("setView", {"lat": float("nan")})


# --- The background has problems (T-MAP-10) -------------------------------------------------


@pytest.mark.parametrize(
    ("problem", "expected"),
    [
        (TileProblem.KEY_REJECTED, ("Tracestrack refused the key", "Check the key in Settings")),
        (TileProblem.CREDIT_EXHAUSTED, ("credit is used up", "Check your Tracestrack account")),
        (TileProblem.NETWORK_UNAVAILABLE, ("cannot be loaded", "Check the connection")),
        (TileProblem.INVALID_RESPONSE, ("cannot be shown", "Try again later")),
    ],
)
def test_when_the_provider_fails_the_tracks_stay_and_the_notice_says_what_to_do(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    problem: TileProblem,
    expected: tuple[str, str],
) -> None:
    keys = InMemoryTileKeyStore()
    keys.save(TileKey(FakeTileSource.ACCEPTED_KEY))  # a key: the notice is the provider's problem
    tiles = TileFakes(keys, FakeTileSource(keys, script=[TileUnavailable(problem)]))
    window, view = window_with_page(qtbot, tmp_path, settings, tiles)
    show_view(qtbot, window, NEAR)
    page = window.map_page

    what, remedy = expected
    qtbot.waitUntil(lambda: page.notice_visible and what in page.notice_text, timeout=WAIT_MS)

    assert remedy in page.notice_text  # not only what is wrong: what to do about it
    qtbot.waitUntil(lambda: page_state(qtbot, view)["tracks"] == 8, timeout=WAIT_MS)  # tracks stay
    if problem is not TileProblem.INVALID_RESPONSE:  # that one is per tile, the others global
        assert len(tiles.source.calls) <= 3  # the proxy stops asking: no hammering


def test_without_a_key_the_notice_appears_at_once_and_the_list_still_works(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, _view = window_with_page(qtbot, tmp_path, settings)  # the fake key store is empty
    show_view(qtbot, window, NEAR)

    assert window.map_page.notice_visible
    assert "needs a Tracestrack key" in window.map_page.notice_text
    assert window.map_page.list_model.rowCount() == 8


def test_a_tile_that_arrives_clears_the_notice(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    tiles = keyed_tiles()
    window, _view = window_with_page(qtbot, tmp_path, settings, tiles)
    qtbot.waitUntil(lambda: len(tiles.source.calls) > 0, timeout=WAIT_MS)

    assert not window.map_page.notice_visible
