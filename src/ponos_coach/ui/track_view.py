# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map of one activity: its GPS track on the tile background, in the activity-detail window.

`TrackView` is a `ChartView` (same engine, same isolation: app scheme only, interceptor, no context
menu, no message channel) that shows the small page `track.html`. A page that only draws needs
no `QWebChannel`, so it has none: Python sends the points once as JSON (`track_script`) and the
page answers nothing. The tiles come from the same local proxy as the map (`ponos://tiles/...`),
which holds the Tracestrack key: the key never enters the page. Without a key or without network
the tiles fail, the page keeps its neutral background with the track, and `TrackPanel` says why
in one line.

Positions are personal data: nothing here logs them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ponos_coach.load.activity_detail import TrackOutline
from ponos_coach.maps.ports import TileProblem
from ponos_coach.ui import map_texts
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.map_style import page_colors
from ponos_coach.ui.tokens import ThemeTokens
from ponos_coach.ui.web_policy import APP_TRACK_URL, TILE_URL_TEMPLATE

_COORDINATE_DECIMALS = 5  # about one metre
_JSON_SEPARATORS = (",", ":")
_TRACK_COLOR_SLOT = 0  # the first category colour (Okabe-Ito blue), as running on the main map


@dataclass(frozen=True, slots=True)
class TrackStrings:
    """The texts the page shows or exposes to assistive technology, already translated."""

    map: str
    zoom_in: str
    zoom_out: str
    start: str
    finish: str


def track_script(outline: TrackOutline, tokens: ThemeTokens, strings: TrackStrings) -> str:
    """JavaScript that draws the track: its single argument is a JSON made by `json.dumps`."""
    payload: dict[str, object] = {
        "tileUrl": TILE_URL_TEMPLATE,
        "attribution": map_texts.ATTRIBUTION,
        "colors": {**page_colors(tokens), "track": tokens.categories[_TRACK_COLOR_SLOT]},
        "strings": {
            "map": strings.map,
            "zoomIn": strings.zoom_in,
            "zoomOut": strings.zoom_out,
            "start": strings.start,
            "finish": strings.finish,
        },
        "lat": [round(v, _COORDINATE_DECIMALS) for v in outline.lat_deg.tolist()],
        "lon": [round(v, _COORDINATE_DECIMALS) for v in outline.lon_deg.tolist()],
        "segment": outline.segment.tolist(),
    }
    body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=_JSON_SEPARATORS)
    return f"ponosTrack.show({body});"


class TrackView(ChartView):
    """The track page. `show_outline` draws a track as soon as the page is ready."""

    _page_url = APP_TRACK_URL

    def show_outline(
        self, outline: TrackOutline, tokens: ThemeTokens, strings: TrackStrings
    ) -> None:
        """Draw the track (the page is created now, if it was not)."""
        self.start_engine()
        if self._page is None:  # closed: nothing to draw
            return
        self._page.setBackgroundColor(QColor(tokens.map_background))
        self.run_script(track_script(outline, tokens, strings))


class TrackPanel(QWidget):
    """The track view and, under it, one line when the tiles cannot be had."""

    def __init__(
        self, engine: ChartEngine, accessible_name: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._view = TrackView(engine, accessible_name, self)
        self._problem = QLabel(self)
        self._problem.setObjectName("trackTileProblem")
        self._problem.setTextFormat(Qt.TextFormat.PlainText)
        self._problem.setWordWrap(True)
        self._problem.setVisible(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._view, stretch=1)
        layout.addWidget(self._problem)
        tiles = engine.tiles
        if tiles is not None:
            tiles.problem_changed.connect(self.show_tile_problem)
            self.show_tile_problem(tiles.problem)

    @property
    def view(self) -> TrackView:
        """The web view."""
        return self._view

    @property
    def tile_problem_text(self) -> str:
        """The line about the tiles (empty if there is no problem)."""
        return "" if self._problem.isHidden() else self._problem.text()

    def show_tile_problem(self, problem: object) -> None:
        """Say in one line why the background is plain, or hide the line."""
        if not isinstance(problem, TileProblem):
            self._problem.setVisible(False)
            return
        message = map_texts.tile_notice(problem)
        self._problem.setText(message.title.rstrip(".") + ".")
        self._problem.setVisible(True)
