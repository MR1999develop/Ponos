# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map half of the Activities section: the map with the tracks, the count and totals of the
visible area, the legend and the data of the selected activity.

A humble view: the controller decides what is asked and the answers arrive as signals; this page
keeps the last answers, shows them and forwards the user's gestures (a click on the map, the
choice of how to colour the track, the pointer over the elevation profile or over the track).
Nothing is sent to the web page before `start_engines` (the window shows first); then the
page is given the whole state again, so it never matters when it was created.
"""

from __future__ import annotations

from collections.abc import Callable
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QDateTime, QLocale, QSettings, Qt, QTime
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import ActivityIdentity, ActivityRecord, Sport
from ponos_coach.activities.map_queries import TRACK_MIN_ZOOM, AreaView, SearchResult
from ponos_coach.load.activity_profile import ActivityProfileData, NoTrack, ZoneAbsence
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.maps.domain import BACKGROUNDS, MapBackground
from ponos_coach.maps.ports import TileProblem
from ponos_coach.nutrition.domain import ActivityEnergy
from ponos_coach.ui import map_texts as texts
from ponos_coach.ui.activity_text import (
    active_calories_caption,
    active_calories_text,
    ascent_m_text,
    distance_km_text,
    duration_text,
    sport_text,
)
from ponos_coach.ui.chart_payload import prepare_figure
from ponos_coach.ui.chart_texts import chart_chrome
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.elevation_figure import (
    EXPORT_FILE_STEM,
    ElevationLabels,
    build_elevation_figure,
)
from ponos_coach.ui.map_background import BackgroundChoice
from ponos_coach.ui.map_controller import MapController
from ponos_coach.ui.map_legend import LegendBar, LegendEntry
from ponos_coach.ui.map_messages import (
    MapView,
    background_message,
    profile_view_message,
)
from ponos_coach.ui.map_style import page_colors, page_sports, sport_color, sport_look
from ponos_coach.ui.map_web_view import MapWebView
from ponos_coach.ui.tile_service import TileService
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for
from ponos_coach.ui.web_policy import tile_url_template

KEY_COLOR_MODE = "map/colorMode"
MODE_SPORT = "sport"
MODE_ZONES = "zones"
_INITIAL_VIEW = {"lat": 30.0, "lon": 10.0, "zoom": 2}  # the world, not a place of the user
_DETAIL_MIN_HEIGHT = 110
_MAP_MIN_HEIGHT = 220  # px: below this a map of tracks cannot be read
# Map over elevation profile: the profile gets a real part of the pane (user's request of
# 2026-11-10), 30% lower than the 2/5 it first had (user's request of 2026-11-11): 28% of the
# pane, the map 72% and its minimum.
MAP_SHARE = (18, 7)
_MS_TO_KMH = 3.6
_STACK_CHART = 0
_STACK_MESSAGE = 1
type _Profile = ActivityProfileData | NoTrack | ExcludedActivity | None


def _speed_text(distance_m: float | None, moving_s: float) -> str:
    """Average speed over the moving time, one decimal; empty without a distance."""
    if distance_m is None or moving_s <= 0:
        return ""
    return QLocale().toString(distance_m / moving_s * _MS_TO_KMH, "f", 1)


class MapPage(QWidget):
    """The map section."""

    def __init__(  # noqa: PLR0913 - every argument is a distinct collaborator of the page
        self,
        engine: ChartEngine,
        controller: MapController,
        *,
        tiles: TileService | None,
        background: BackgroundChoice,
        settings: QSettings,
        theme: Callable[[], Theme],
        zone: Callable[[], ZoneInfo],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._settings = settings
        self._theme = theme
        self._zone = zone
        self._tiles = tiles
        self._background = background
        self._started = False
        self._syncing = False
        self._fit_pending = False
        self._profile_sent: int | None = None  # generation of the points the page holds
        self._search: SearchResult | None = None
        self._area: AreaView | None = None
        self._profile: _Profile = None
        self._energy: tuple[ActivityIdentity, ActivityEnergy | None] | None = None
        self._selected: ActivityIdentity | None = None
        self._tile_problem: TileProblem | None = None
        self._map = MapWebView(engine, self.tr("Map of activities"), self)
        self._chart = ChartView(engine, self.tr("Elevation profile of the selected activity"), self)
        self._build()
        self._connect(controller, tiles)
        self._update_texts()

    # --- Construction ---------------------------------------------------------------------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_notice())
        outer.addWidget(self._build_area_summary())
        vertical = QSplitter(Qt.Orientation.Vertical, self)
        self._vertical = vertical
        self._sized = False  # the proportions are applied at the first show, when the room is known
        top = QWidget(vertical)
        top_column = QVBoxLayout(top)
        top_column.setContentsMargins(0, 0, 0, 0)
        top_column.setSpacing(0)
        # The map keeps a usable height: in a quarter of the window the elevation profile below
        # must never squeeze it away (user's report of 2026-11-09).
        self._map.setMinimumHeight(_MAP_MIN_HEIGHT)
        top_column.addWidget(self._map, stretch=1)
        self._sport_legend = LegendBar(self.tr("Legend: sports"), top)
        self._zone_legend = LegendBar(self.tr("Legend: heart-rate zones"), top)
        top_column.addWidget(self._sport_legend)
        top_column.addWidget(self._zone_legend)
        vertical.addWidget(top)
        vertical.addWidget(self._build_detail())
        for index, weight in enumerate(MAP_SHARE):
            vertical.setStretchFactor(index, weight)
        vertical.setChildrenCollapsible(False)
        outer.addWidget(vertical, stretch=1)

    def _build_notice(self) -> QWidget:
        self._notice = QWidget(self)
        self._notice.setObjectName("mapNotice")
        row = QHBoxLayout(self._notice)
        row.setContentsMargins(SPACE_4, SPACE_2, SPACE_4, SPACE_2)
        self._notice_text = QLabel(self._notice)
        self._notice_text.setTextFormat(Qt.TextFormat.PlainText)
        self._notice_text.setWordWrap(True)
        self._notice_text.setAccessibleName(self.tr("Map background notice"))
        row.addWidget(self._notice_text, stretch=1)
        self._notice.hide()
        return self._notice

    def _build_detail(self) -> QWidget:
        detail = QWidget(self)
        detail.setMinimumHeight(_DETAIL_MIN_HEIGHT)
        column = QVBoxLayout(detail)
        column.setContentsMargins(SPACE_4, SPACE_2, SPACE_4, SPACE_2)
        column.setSpacing(SPACE_1)
        header = QHBoxLayout()
        self._detail_title = QLabel(detail)
        self._detail_title.setTextFormat(Qt.TextFormat.PlainText)
        self._detail_title.setWordWrap(True)
        header.addWidget(self._detail_title, stretch=1)
        label = QLabel(texts.color_mode_label(), detail)
        self._color_mode = QComboBox(detail)
        self._color_mode.setObjectName("mapColorMode")
        self._color_mode.setAccessibleName(texts.color_mode_label())
        label.setBuddy(self._color_mode)
        self._color_mode.addItem(texts.color_by_sport_text(), MODE_SPORT)
        self._color_mode.addItem(texts.color_by_zone_text(), MODE_ZONES)
        self._color_mode.currentIndexChanged.connect(self._on_mode_chosen)
        header.addWidget(label)
        header.addWidget(self._color_mode)
        column.addLayout(header)
        self._detail_stack = QStackedWidget(detail)
        self._detail_stack.addWidget(self._chart)
        self._detail_message = QLabel(detail)
        self._detail_message.setTextFormat(Qt.TextFormat.PlainText)
        self._detail_message.setWordWrap(True)
        self._detail_message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_stack.addWidget(self._detail_message)
        body = QHBoxLayout()
        body.setSpacing(SPACE_4)
        body.addWidget(self._detail_stack, stretch=1)
        body.addWidget(self._build_summary(detail))
        column.addLayout(body, stretch=1)
        return detail

    def _build_summary(self, parent: QWidget) -> QWidget:
        """The data of the selected activity, beside its elevation profile."""
        self._summary = QWidget(parent)
        self._summary.setObjectName("mapActivitySummary")
        column = QVBoxLayout(self._summary)
        column.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_1)
        self._summary_values: dict[str, QLabel] = {}
        for key, text in self._summary_rows():
            value = QLabel(self._summary)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setObjectName(f"mapSummary-{key}")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self._summary_values[key] = value
            form.addRow(text, value)
        column.addLayout(form)
        column.addStretch(1)
        self._summary.hide()
        return self._summary

    def _summary_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("moving", self.tr("Moving time (h:mm:ss)")),
            ("elapsed", self.tr("Elapsed time (h:mm:ss)")),
            ("distance", self.tr("Distance (km)")),
            ("ascent", self.tr("Ascent (m)")),
            ("speed", self.tr("Average speed (km/h)")),
            ("calories", active_calories_caption()),
        )

    def _build_area_summary(self) -> QWidget:
        """The count and the totals of the activities that start in the visible area."""
        summary = QWidget(self)
        summary.setObjectName("areaSummary")
        column = QVBoxLayout(summary)
        column.setContentsMargins(SPACE_4, SPACE_2, SPACE_4, SPACE_2)
        column.setSpacing(SPACE_1)
        self._count = QLabel(summary)
        font = self._count.font()
        font.setWeight(font.Weight.DemiBold)
        self._count.setFont(font)
        self._totals = QLabel(summary)
        self._extra = QLabel(summary)
        for label in (self._count, self._totals, self._extra):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
        self._count.setObjectName("areaCount")
        self._totals.setObjectName("areaTotals")
        self._extra.setObjectName("areaExtra")
        column.addWidget(self._count)
        column.addWidget(self._totals)
        column.addWidget(self._extra)
        return summary

    def _connect(self, controller: MapController, tiles: TileService | None) -> None:
        controller.profile_opened.connect(self.prepare_for_profile)
        controller.search_changed.connect(self._on_search)
        controller.area_changed.connect(self._on_area)
        controller.selection_changed.connect(self._on_selection)
        controller.profile_changed.connect(self._on_profile)
        controller.energy_changed.connect(self._on_energy)
        bridge = self._map.bridge
        bridge.view_changed.connect(controller.set_view)
        bridge.activity_clicked.connect(controller.select_index)
        bridge.activity_opened.connect(controller.open_index)
        bridge.point_hovered.connect(self._on_map_point)
        bridge.point_left.connect(self._on_map_point_left)
        self._chart.hovered.connect(self._on_chart_point)
        self._chart.hover_left.connect(self._on_chart_point_left)
        bridge.background_chosen.connect(self._background.choose)
        self._background.changed.connect(self._on_background_changed)
        if tiles is not None:
            tiles.problem_changed.connect(self._on_tile_problem)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 (Qt name)
        """At the first show the map and the elevation profile take their proportions of the
        room they really have."""
        super().showEvent(event)
        if self._sized:
            return
        self._sized = True
        room = sum(self._vertical.sizes())
        self._vertical.setSizes([room * w // sum(MAP_SHARE) for w in MAP_SHARE])

    # --- Access for the window and the tests ------------------------------------------------

    @property
    def map_view(self) -> MapWebView:
        """The map's web view."""
        return self._map

    @property
    def chart(self) -> ChartView:
        """The elevation profile's web view."""
        return self._chart

    @property
    def map_and_profile(self) -> QSplitter:
        """The splitter with the map (and its legends) above the elevation profile."""
        return self._vertical

    @property
    def sport_legend(self) -> LegendBar:
        """Legend of the sports (colour and dash)."""
        return self._sport_legend

    @property
    def zone_legend(self) -> LegendBar:
        """Legend of the heart-rate zones, when the track is coloured by zone."""
        return self._zone_legend

    @property
    def color_mode(self) -> QComboBox:
        """The choice of how to colour the selected track."""
        return self._color_mode

    @property
    def notice_visible(self) -> bool:
        """Whether the notice about the map background is shown."""
        return not self._notice.isHidden()

    @property
    def notice_text(self) -> str:
        """The notice's text."""
        return self._notice_text.text()

    @property
    def summary_texts(self) -> tuple[str, str, str]:
        """The count, the totals and the extra line above the map."""
        return self._count.text(), self._totals.text(), self._extra.text()

    @property
    def detail_texts(self) -> tuple[str, str]:
        """The detail's title and, if the chart is not shown, why."""
        return self._detail_title.text(), self._detail_message.text()

    @property
    def shows_chart(self) -> bool:
        """Whether the elevation profile is the visible part of the detail."""
        return self._detail_stack.currentIndex() == _STACK_CHART

    @property
    def effective_mode(self) -> str:
        """How the selected track is coloured now (the choice, if the activity allows it)."""
        profile = self._profile
        if self._preferred_mode() == MODE_ZONES and _has_zones(profile):
            return MODE_ZONES
        return MODE_SPORT

    # --- Start -------------------------------------------------------------------------------

    def start_engines(self) -> None:
        """Create the web pages (after the window is shown) and give them the whole state."""
        if self._started:
            return
        self._started = True
        self._map.start_engine()
        self._chart.start_engine()
        self._push_all()

    def request_fit(self) -> None:
        """Frame all the filtered activities (the "Show all activities" command)."""
        self._call("fitAll", {})

    def refresh_tiles(self) -> None:
        """Ask the tiles again (the key was changed or removed)."""
        self._call("refreshTiles", {})

    def zoom_by(self, delta: int) -> None:
        """Zoom the map in (+1) or out (-1)."""
        self._call("zoomBy", {"delta": delta})

    def apply_theme(self) -> None:
        """The colour scheme changed: colours of the page, the legends and the chart."""
        tokens = tokens_for(self._theme())
        self._map.set_background(tokens.map_background)
        self._call("setTheme", page_colors(tokens))
        self._update_legends()
        self._show_profile()

    def show_tile_problem(self, problem: object) -> None:
        """Show the notice for a problem of the background in use, or hide it (`None`)."""
        self._tile_problem = problem if isinstance(problem, TileProblem) else None
        if self._tile_problem is None:
            self._notice.hide()
            return
        message = texts.tile_notice(self._tile_problem, self._background.current)
        self._notice_text.setText(texts.notice_line(message))
        self._notice.show()

    def _on_tile_problem(self, background: object, problem: object) -> None:
        """A server's problem changed: it is shown only if it is the background in use."""
        if background == self._background.current:
            self.show_tile_problem(problem)

    def _on_background_changed(self, background: object) -> None:
        """The user chose another background (menu or control): the page switches without
        reloading, and the notice is the one of the new background's server."""
        if not isinstance(background, MapBackground):
            return
        self._call("setBackground", _background_payload(background))
        self.show_tile_problem(None if self._tiles is None else self._tiles.problem_of(background))

    # --- Answers from the controller ---------------------------------------------------------

    def prepare_for_profile(self) -> None:
        """A profile was opened: the next search frames its activities."""
        self._fit_pending = True

    def _on_search(self, result: object) -> None:
        self._search = result if isinstance(result, SearchResult) else None
        count = 0 if self._search is None else len(self._search.activities)
        self._map.bridge.activity_count = count
        self._map.bridge.activity_generation = self._controller.list_generation
        self._update_legends()
        self._update_texts()
        if self._started:
            self._push_starts()
            self._on_selection(self._selected, False)  # positions in the list changed
            if isinstance(self._profile, ActivityProfileData):
                self._send_profile_view(self._profile)
            if self._fit_pending and self._search is not None and count:
                self._fit_pending = False
                self._call("fitAll", {})

    def _on_area(self, view: object) -> None:
        self._area = view if isinstance(view, AreaView) else None
        self._update_texts()
        self._push_tracks()

    def _on_selection(self, identity: object, reveal: object) -> None:
        self._selected = identity if isinstance(identity, ActivityIdentity) else None
        position = None if self._selected is None else self._controller.index_of(self._selected)
        self._call("setSelected", {"idx": position, "reveal": reveal is True})
        self._update_detail_title()

    def focus_selected(self) -> None:
        """Show the whole track of the selected activity (its start until the points arrive)."""
        self._call("focusSelected", {})

    def _on_profile(self, outcome: object) -> None:
        if isinstance(outcome, ActivityProfileData | NoTrack | ExcludedActivity):
            self._profile = outcome
        else:
            self._profile = None
        self._map.bridge.profile_points = (
            len(self._profile) if isinstance(self._profile, ActivityProfileData) else 0
        )
        self._map.bridge.profile_generation = self._controller.profile_generation
        self._show_profile()

    # --- Detail: elevation profile and track colour ------------------------------------------

    def _preferred_mode(self) -> str:
        raw = self._settings.value(KEY_COLOR_MODE, MODE_SPORT)
        return MODE_ZONES if raw == MODE_ZONES else MODE_SPORT

    def _on_mode_chosen(self, row: int) -> None:
        if self._syncing:
            return
        mode = self._color_mode.itemData(row)
        self._settings.setValue(KEY_COLOR_MODE, MODE_ZONES if mode == MODE_ZONES else MODE_SPORT)
        self._show_profile()

    def _show_profile(self) -> None:
        profile = self._profile
        self._update_mode_choice(profile)
        self._update_detail_title()
        self._update_legends()
        if isinstance(profile, ActivityProfileData):
            self._show_profile_data(profile)
        else:
            self._show_message(self._profile_message(profile))
            self._call("clearProfile", {})

    def _show_profile_data(self, profile: ActivityProfileData) -> None:
        mode = self.effective_mode
        self._send_profile(profile)
        if profile.altitude_m is None:
            self._show_message(texts.no_altitude_text())
            return
        tokens = tokens_for(self._theme())
        figure = build_elevation_figure(
            profile, self._elevation_labels(profile), tokens, by_zone=mode == MODE_ZONES
        )
        prepared = prepare_figure(figure, chart_chrome(), EXPORT_FILE_STEM, tokens, hover=True)
        self._detail_stack.setCurrentIndex(_STACK_CHART)
        if self._started:
            self._chart.show_prepared(prepared)

    def _send_profile(self, profile: ActivityProfileData) -> None:
        """Give the map the points of the selected activity (and how to colour them).

        The points come as the script the data thread built; they are sent once per profile.
        """
        script = self._controller.profile_script
        generation = self._controller.profile_generation
        if self._started and script is not None and generation != self._profile_sent:
            self._map.run_script(script)
            self._profile_sent = generation
        self._send_profile_view(profile)

    def _send_profile_view(self, profile: ActivityProfileData) -> None:
        position = self._controller.index_of(profile.identity)
        self._call("setProfileView", profile_view_message(position, self.effective_mode))

    def _elevation_labels(self, profile: ActivityProfileData) -> ElevationLabels:
        x_axis, y_axis = texts.elevation_axis_titles()
        names = tuple(texts.zone_legend_text(i, profile.zone_starts_bpm) for i in range(5))
        return ElevationLabels(
            x_axis,
            y_axis,
            texts.elevation_series_name(),
            (names[0], names[1], names[2], names[3], names[4]),
            texts.no_zone_legend_text(),
            chart_chrome(),
        )

    def _profile_message(self, profile: _Profile) -> str:
        if profile is None:
            return texts.select_prompt_text()
        if isinstance(profile, NoTrack):
            return texts.no_track_text()
        if isinstance(profile, ExcludedActivity):
            return texts.unusable_file_text(profile.reason)
        return ""

    def _show_message(self, message: str) -> None:
        self._detail_message.setText(message)
        self._detail_stack.setCurrentIndex(_STACK_MESSAGE)

    def _update_mode_choice(self, profile: _Profile) -> None:
        """Show the saved choice; the zones entry is disabled, with the reason, if it cannot be."""
        item = self._color_mode.model().item(1)  # type: ignore[attr-defined]
        reason = _zone_reason(profile)
        self._syncing = True
        try:
            item.setEnabled(reason is None)
            item.setToolTip(reason or "")
            self._color_mode.setToolTip(reason or "")
            self._color_mode.setCurrentIndex(1 if self.effective_mode == MODE_ZONES else 0)
            self._color_mode.setEnabled(isinstance(profile, ActivityProfileData))
        finally:
            self._syncing = False

    def _update_detail_title(self) -> None:
        record = self._record_of(self._selected)
        self._update_summary(record)
        if record is None:
            self._detail_title.setText("")
            return
        parts = [
            sport_text(record.sport),
            _start_text(record, self._zone()),
            _with_unit(distance_km_text(record.summary.distance_m), "km"),
            _with_unit(ascent_m_text(record.summary.ascent_m), "m"),
        ]
        self._detail_title.setText("  |  ".join(part for part in parts if part))

    def _update_summary(self, record: ActivityRecord | None) -> None:
        """Fill the data beside the elevation profile; hidden with no activity selected."""
        self._summary.setVisible(record is not None)
        if record is None:
            return
        summary = record.summary
        values = {
            "moving": duration_text(record.timer_s),
            "elapsed": duration_text(record.elapsed_s),
            "distance": distance_km_text(summary.distance_m),
            "ascent": ascent_m_text(summary.ascent_m),
            "speed": _speed_text(summary.distance_m, record.timer_s),
            "calories": self._energy_text(record),
        }
        for key, text in values.items():
            self._summary_values[key].setText(text)

    def _energy_text(self, record: ActivityRecord) -> str:
        """Active calories of the record, empty until the data thread has answered for it."""
        held = self._energy
        if held is None or held[0] != record.identity:
            return ""
        return active_calories_text(held[1])

    def _on_energy(self, identity: object, energy: object) -> None:
        """The active calories of an activity arrived; they count only for the selected one."""
        if not isinstance(identity, ActivityIdentity) or identity != self._selected:
            return
        self._energy = (identity, energy if isinstance(energy, ActivityEnergy) else None)
        self._update_summary(self._record_of(self._selected))

    def summary_text(self, key: str) -> str:
        """One value of the data beside the elevation profile (empty when it is hidden)."""
        return "" if self._summary.isHidden() else self._summary_values[key].text()

    def _record_of(self, identity: ActivityIdentity | None) -> ActivityRecord | None:
        search = self._search
        if identity is None or search is None:
            return None
        position = self._controller.index_of(identity)
        return None if position is None else search.activities[position]

    # --- Linked cursor: profile and map ------------------------------------------------------

    def _on_chart_point(self, index: int) -> None:
        profile = self._profile
        if isinstance(profile, ActivityProfileData) and 0 <= index < len(profile):
            self._call("setCursor", {"index": index})

    def _on_chart_point_left(self) -> None:
        self._call("setCursor", {"index": None})

    def _on_map_point(self, index: int) -> None:
        profile = self._profile
        if isinstance(profile, ActivityProfileData) and 0 <= index < len(profile):
            km = float(profile.distance_m[index]) / 1000.0
            self._chart.run_script(f"ponosCursor({km!r});")

    def _on_map_point_left(self) -> None:
        self._chart.run_script("ponosCursor(null);")

    # --- Texts and legends -------------------------------------------------------------------

    def _update_texts(self) -> None:
        area = self._area
        search = self._search
        if search is None or not search.activities:
            self._count.setText(texts.no_activities_text())
            self._totals.setText("")
            self._extra.setText("")
            return
        if area is None:
            self._count.setText("")
            self._totals.setText("")
            self._extra.setText("")
            return
        in_area = area.in_area
        self._count.setText(texts.area_count_text(in_area.totals.count))
        self._totals.setText(texts.area_totals_text(in_area.totals) if in_area.totals.count else "")
        self._extra.setText(self._extra_text(area, search))

    def _extra_text(self, area: AreaView, search: SearchResult) -> str:
        lines: list[str] = []
        if area.in_area.totals.count == 0:
            lines.append(texts.empty_area_text())
        if search.without_position:
            lines.append(texts.without_position_text(search.without_position))
        if area.tracks_omitted:
            lines.append(texts.tracks_omitted_text(area.tracks_omitted))
        elif not area.tracks and _zoom_of(self._controller.view) < TRACK_MIN_ZOOM:
            lines.append(texts.zoom_for_tracks_text())
        return "\n".join(lines)

    def _update_legends(self) -> None:
        tokens = tokens_for(self._theme())
        found = set() if self._search is None else {r.sport for r in self._search.activities}
        sports = [s for s in Sport if s in found]
        self._sport_legend.set_entries(
            [LegendEntry(sport_text(s), sport_color(s, tokens), sport_look(s).dash) for s in sports]
        )
        profile = self._profile
        if self.effective_mode == MODE_ZONES and isinstance(profile, ActivityProfileData):
            entries = [
                LegendEntry(texts.zone_legend_text(i, profile.zone_starts_bpm), tokens.zones[i])
                for i in range(len(tokens.zones))
            ]
            entries.append(LegendEntry(texts.no_zone_legend_text(), tokens.no_zone, "4 6"))
            self._zone_legend.set_entries(entries)
        else:
            self._zone_legend.set_entries([])

    # --- Sending to the page -----------------------------------------------------------------

    def _call(self, function: str, payload: object) -> None:
        if self._started:
            self._map.call(function, payload)

    def _page_config(self) -> dict[str, object]:
        tokens = tokens_for(self._theme())
        return {
            "background": _background_payload(self._background.current),
            "backgrounds": [{"key": b.key, "label": b.label} for b in BACKGROUNDS],
            "colors": page_colors(tokens),
            "sports": page_sports(tokens),
            "zoneColors": list(tokens.zones),
            "noZoneColor": tokens.no_zone,
            "locale": QLocale().bcp47Name(),
            "strings": {
                "map": self.tr("Map of activities"),
                "zoomIn": self.tr("Zoom in"),
                "zoomOut": self.tr("Zoom out"),
                "background": self.tr("Map background"),
            },
            "view": _INITIAL_VIEW,
        }

    def _push_all(self) -> None:
        self._profile_sent = None  # a new page has none of the points
        self._map.set_background(tokens_for(self._theme()).map_background)
        self._call("init", self._page_config())
        self._push_starts()
        self._push_tracks()
        self._on_selection(self._selected, False)
        self._show_profile()

    def _push_starts(self) -> None:
        """The starts: the script the data thread built (nothing is encoded here)."""
        script = self._controller.starts_script
        if self._search is None or script is None:
            generation = self._controller.list_generation
            self._call("setStarts", {"gen": generation, "data": []})
        elif self._started:
            self._map.run_script(script)

    def _push_tracks(self) -> None:
        script = self._controller.tracks_script
        if self._search is None or self._area is None or script is None:
            self._call("setTracks", {"gen": self._controller.list_generation, "tracks": []})
        elif self._started:
            self._map.run_script(script)


def _background_payload(background: MapBackground) -> dict[str, object]:
    return background_message(background, tile_url_template(background))


def _start_text(record: ActivityRecord, zone: ZoneInfo) -> str:
    local = record.start.astimezone(zone)
    stamp = QDateTime(
        QDate(local.year, local.month, local.day), QTime(local.hour, local.minute, local.second)
    )
    return QLocale().toString(stamp, QLocale.FormatType.ShortFormat)


def _zoom_of(view: MapView | None) -> int:
    return 0 if view is None else view.zoom


def _has_zones(profile: _Profile) -> bool:
    return isinstance(profile, ActivityProfileData) and profile.zone is not None


def _with_unit(text: str, unit: str) -> str:
    return f"{text} {unit}" if text else ""


def _zone_reason(profile: _Profile) -> str | None:
    """Why the track cannot be coloured by zone (`None` if it can)."""
    if not isinstance(profile, ActivityProfileData):
        return texts.select_prompt_text()
    if profile.zone is None:
        absence = profile.zone_absence or ZoneAbsence.NO_HEART_RATE
        return texts.zone_absence_text(absence)
    return None
