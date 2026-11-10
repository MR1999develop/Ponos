# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map's web view: the isolated page with Leaflet and its small, validated message channel.

It is a `ChartView` (same engine, same isolation: app scheme only, interceptor, no context menu)
with one addition the plan declares: a `QWebChannel` whose single object, `MapBridge`, has a handful
of slots that take only numbers. Whatever the page sends is validated here (`map_messages`) and
becomes a typed signal, or is dropped and counted; the page names an activity only by its position
in the list Python sent, so it cannot name anything else. Python talks to the page through a closed
set of functions with JSON arguments (`page_call`).
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QWidget

from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.map_messages import page_call, parse_activity_index, parse_view
from ponos_coach.ui.web_policy import APP_MAP_URL

BRIDGE_NAME = "bridge"


class MapBridge(QObject):
    """What the page may tell Python: it is ready, the view moved, an activity was clicked, the
    pointer is over (or left) a point of the selected track.

    `activity_count` and `profile_points` are how many activities and profile points Python sent;
    `activity_generation` and `profile_generation` number those lists. A message outside them is
    dropped (`rejected` counts them), and so is one that carries the number of an older list
    (`stale` counts them): the page may answer about a list that Python has already replaced.
    """

    ready = Signal()
    view_changed = Signal(object)  # MapView
    activity_clicked = Signal(int)
    activity_opened = Signal(int)
    point_hovered = Signal(int)
    point_left = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.activity_count = 0
        self.profile_points = 0
        self.activity_generation = 0
        self.profile_generation = 0
        self.rejected = 0
        self.stale = 0

    @Slot()
    def pageReady(self) -> None:  # noqa: N802 - called from JavaScript
        """The page loaded its scripts and opened the channel."""
        self.ready.emit()

    @Slot(float, float, float, float, int)
    def viewChanged(  # noqa: N802 - called from JavaScript
        self, south: float, west: float, north: float, east: float, zoom: int
    ) -> None:
        """The map moved or zoomed."""
        view = parse_view(south, west, north, east, zoom)
        if view is None:
            self.rejected += 1
            return
        self.view_changed.emit(view)

    @Slot(int, int)
    def activityClicked(  # noqa: N802 - called from JavaScript
        self, position: int, generation: int
    ) -> None:
        """An activity (a start or a track) was clicked; `generation` is the list it came from."""
        index = self._checked_activity(position, generation)
        if index is not None:
            self.activity_clicked.emit(index)

    @Slot(int, int)
    def activityOpened(  # noqa: N802 - called from JavaScript
        self, position: int, generation: int
    ) -> None:
        """An activity (a start or the selected track) was double clicked: open its window."""
        index = self._checked_activity(position, generation)
        if index is not None:
            self.activity_opened.emit(index)

    def _checked_activity(self, position: int, generation: int) -> int | None:
        """The position if it names an activity of the current list; counted and None if not."""
        if generation != self.activity_generation:
            self.stale += 1
            return None
        index = parse_activity_index(position, self.activity_count)
        if index is None:
            self.rejected += 1
        return index

    @Slot(int, int)
    def pointHovered(  # noqa: N802 - called from JavaScript
        self, index: int, generation: int
    ) -> None:
        """The pointer is over a point of the selected activity's track (of that profile)."""
        if generation != self.profile_generation:
            self.stale += 1
            return
        point = parse_activity_index(index, self.profile_points)
        if point is None:
            self.rejected += 1
            return
        self.point_hovered.emit(point)

    @Slot()
    def pointLeft(self) -> None:  # noqa: N802 - called from JavaScript
        """The pointer left the selected activity's track."""
        self.point_left.emit()


class MapWebView(ChartView):
    """The map page. Create it like a chart view; `call` sends a function and its JSON argument."""

    _page_url = APP_MAP_URL

    def __init__(
        self, engine: ChartEngine, accessible_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(engine, accessible_name, parent)
        self._bridge = MapBridge(self)
        self._channel = QWebChannel(self)
        self._channel.registerObject(BRIDGE_NAME, self._bridge)
        self._background = QColor("#E6E8EB")

    @property
    def bridge(self) -> MapBridge:
        """The object the page talks to."""
        return self._bridge

    def set_background(self, color: str) -> None:
        """Colour shown before the page draws (the map's neutral background)."""
        self._background = QColor(color)
        if self._page is not None:
            self._page.setBackgroundColor(self._background)

    def call(self, function: str, payload: object) -> None:
        """Call one of the page's functions (queued until the page is loaded)."""
        self.run_script(page_call(function, payload))

    def _prepare_page(self, page: QWebEnginePage) -> None:
        page.setWebChannel(self._channel)
        page.setBackgroundColor(self._background)
