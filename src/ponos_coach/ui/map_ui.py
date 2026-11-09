# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map and the filters in the window: owns the shared filter, the controller, the page, the
Settings > Map page and the commands of the Map menu.

The window only gives it callbacks and places its widgets (the filter bar above the Activities
section, the map page beside the table). The table of the activities and the map show the same
activities: both follow the one `SharedFilterState`, and a selection made in the table or on the
map is the selection of both. The table can also be narrowed to the activities that start in the
area the map shows ("Only the visible map area"); the map keeps the count and totals of that area.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenuBar, QWidget

from ponos_coach.activities.domain import ActivityIdentity, identity_key
from ponos_coach.activities.map_queries import AreaView, SearchResult
from ponos_coach.ui.activity_filters import SharedFilterState
from ponos_coach.ui.activity_table import ActivitiesPage
from ponos_coach.ui.chart_view import ChartEngine
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.filter_bar import FilterBar
from ponos_coach.ui.map_controller import MapController
from ponos_coach.ui.map_page import MapPage
from ponos_coach.ui.map_query_host import MapQueryHost
from ponos_coach.ui.map_settings import MapSettingsModel
from ponos_coach.ui.map_settings_dialog import MapSettingsDialog
from ponos_coach.ui.settings_page import SettingsPage
from ponos_coach.ui.tile_service import TileService
from ponos_coach.ui.tokens import Theme
from ponos_coach.ui.workspace import WorkspaceController

_KEY_AREA_ONLY = "activities/visibleAreaOnly"


@dataclass(frozen=True, slots=True)
class MapServices:
    """What the Composition Root gives the map: tiles, cache settings, the data-thread
    host of the map queries, and how to release the tile cache."""

    tiles: TileService
    settings: MapSettingsModel
    host: MapQueryHost
    release: Callable[[], None]


class MapUi(QObject):
    """Owns the filters and the map of the window."""

    def __init__(  # noqa: PLR0913 - every argument is a distinct collaborator of the window
        self,
        window: QWidget,
        controller: WorkspaceController,
        settings: QSettings,
        services: MapServices,
        activities: ActivitiesPage,
        *,
        engine: ChartEngine,
        theme: Callable[[], Theme],
        zone: Callable[[], ZoneInfo],
        show_error: Callable[[ErrorMessage], None],
        show_map: Callable[[], None],
        settings_page: SettingsPage,
        show_settings: Callable[[], None],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._activities = activities
        self._show_error = show_error
        self._show_map = show_map
        self._settings_page = settings_page
        self._show_settings = show_settings
        self._settings = settings
        self._on_activities = False
        self._filter_keys: frozenset[str] | None = None  # who passed the filter (None: all)
        self._area_keys: frozenset[str] | None = None  # who starts in the visible area
        self._filters = SharedFilterState(settings, self)
        self._bar = FilterBar(self._filters, theme, window)
        self._controller = MapController(controller, services.host, self._filters, self)
        self._page = MapPage(
            engine,
            self._controller,
            tiles=services.tiles,
            settings=settings,
            theme=theme,
            zone=zone,
            parent=window,
        )
        self._build_actions()
        self._connect()
        self._fill_settings_page()
        self.update_actions(on_activities=False)
        self._activities.area_only_check.setChecked(
            bool(settings.value(_KEY_AREA_ONLY, False, type=bool))
        )

    # --- Parts ----------------------------------------------------------------------------

    @property
    def filters(self) -> SharedFilterState:
        """The one filter that the table and the map share."""
        return self._filters

    @property
    def filter_bar(self) -> FilterBar:
        """The bar above the table and the map."""
        return self._bar

    @property
    def controller(self) -> MapController:
        """The map's controller."""
        return self._controller

    @property
    def page(self) -> MapPage:
        """The map, beside the table of the activities."""
        return self._page

    @property
    def settings_dialog(self) -> MapSettingsDialog | None:
        """The content of the Map settings page."""
        form = self._settings_page.form
        return form if isinstance(form, MapSettingsDialog) else None

    @property
    def actions(self) -> dict[str, QAction]:
        """The commands of the Map menu, by name."""
        return self._actions

    # --- Actions and menu -------------------------------------------------------------------

    def _action(
        self, text: str, shortcut: str | QKeySequence.StandardKey, slot: Callable[[], object]
    ) -> QAction:
        action = QAction(text, self._window)
        action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self._actions = {
            "show": self._action(self.tr("Show activities and map"), "Ctrl+M", self._show_map),
            "fit": self._action(self.tr("Show all activities"), "Ctrl+0", self._page.request_fit),
            "zoom_in": self._action(
                self.tr("Zoom in"), QKeySequence.StandardKey.ZoomIn, lambda: self._page.zoom_by(1)
            ),
            "zoom_out": self._action(
                self.tr("Zoom out"),
                QKeySequence.StandardKey.ZoomOut,
                lambda: self._page.zoom_by(-1),
            ),
            "filter": self._action(self.tr("Filter activities"), "Ctrl+F", self.focus_filters),
            "clear_filters": self._action(
                self.tr("Clear filters"), "Ctrl+Shift+K", self._bar.clear_filters
            ),
            "clear_cache": self._action(
                self.tr("Clear map cache..."), "Ctrl+Shift+Backspace", self.ask_clear_cache
            ),
        }

    def build_menu(self, menu_bar: QMenuBar) -> None:
        """Add the Map menu."""
        menu = menu_bar.addMenu(self.tr("Map"))
        for name in ("show", "fit", "zoom_in", "zoom_out"):
            menu.addAction(self._actions[name])
        menu.addSeparator()
        menu.addAction(self._actions["filter"])
        menu.addAction(self._actions["clear_filters"])
        menu.addSeparator()
        menu.addAction(self._actions["clear_cache"])

    def update_actions(self, *, on_activities: bool) -> None:
        """Enable the commands that apply to the section shown, with the reason when they do not."""
        needs_map = self.tr("Open the Activities section first.")
        for name in ("fit", "zoom_in", "zoom_out"):
            action = self._actions[name]
            action.setEnabled(on_activities)
            action.setToolTip("" if on_activities else needs_map)
        active = not self._filters.criteria.is_empty
        clear = self._actions["clear_filters"]
        clear.setEnabled(active)
        clear.setToolTip("" if active else self.tr("No filter is set."))

    # --- Wiring ------------------------------------------------------------------------------

    def _connect(self) -> None:
        self._controller.search_changed.connect(self._on_search)
        self._controller.selection_changed.connect(self._on_selection)
        self._controller.area_changed.connect(self._on_area)
        self._activities.area_only_toggled.connect(self._on_area_only_toggled)
        self._controller.failed.connect(self._show_error)
        self._activities.selection_changed.connect(self._on_table_selection)
        self._page.settings_requested.connect(self.show_settings)
        self._settings_page.shown.connect(self._fill_settings_page)
        self._filters.changed.connect(
            lambda *_: self.update_actions(on_activities=self._on_activities)
        )

    def _on_search(self, result: object) -> None:
        """The table shows the activities that passed the filter, like the map."""
        if isinstance(result, SearchResult) and not self._filters.criteria.is_empty:
            self._filter_keys = frozenset(identity_key(r.identity) for r in result.activities)
        else:
            self._filter_keys = None
        self._show_table_rows()

    def _on_area(self, view: object) -> None:
        """The map reported a new area: with "Only the visible map area" the table follows it."""
        if isinstance(view, AreaView):
            self._area_keys = frozenset(identity_key(r.identity) for r in view.in_area.activities)
        else:
            self._area_keys = None
        if self._activities.area_only_check.isChecked():
            self._show_table_rows()

    def _on_area_only_toggled(self, on: bool) -> None:
        self._settings.setValue(_KEY_AREA_ONLY, on)
        self._show_table_rows()

    def _show_table_rows(self) -> None:
        """The rows of the table: those that start in the area when asked (the area answer already
        follows the filter), otherwise those that passed the filter."""
        area_only = self._activities.area_only_check.isChecked()
        keys = self._area_keys if area_only and self._area_keys is not None else self._filter_keys
        self._activities.set_visible_identities(keys)

    def _on_table_selection(self) -> None:
        if self._activities.is_syncing:
            return
        selected = self._activities.selected_activity()
        self._controller.select_identity(None if selected is None else selected.identity)

    def _on_selection(self, identity: object, _reveal: object) -> None:
        self._activities.select_identity(
            identity if isinstance(identity, ActivityIdentity) else None
        )

    # --- Window events -----------------------------------------------------------------------

    def section_changed(self, *, on_activities: bool) -> None:
        """The filter bar shows over the table and the map only; the map's commands follow."""
        self._on_activities = on_activities
        self._bar.setVisible(on_activities)
        self.update_actions(on_activities=on_activities)

    def start_engines(self) -> None:
        """Start the map's web pages (after the window is shown)."""
        self._page.start_engines()

    def apply_theme(self) -> None:
        """The colour scheme changed."""
        self._page.apply_theme()

    def focus_filters(self) -> None:
        """The "Filter activities" command: show the table's filters and focus the first field."""
        self._bar.show()
        self._bar.focus_first_field()

    # --- Settings ----------------------------------------------------------------------------

    def show_settings(self) -> None:
        """Open Settings on the Map page."""
        self._show_settings()

    def _fill_settings_page(self) -> None:
        """Show the page's content: built the first time (and after the edits were discarded),
        brought up to date every time the page comes into view."""
        form = self.settings_dialog
        if form is None:
            form = MapSettingsDialog(self._services.settings)
            self._settings_page.show_form(form)
        else:
            form.refresh()

    def ask_clear_cache(self) -> None:
        """The "Clear map cache..." command: the settings page asks, with the size it frees."""
        self.show_settings()
        form = self.settings_dialog
        if form is not None:
            form.ask_clear_cache()

    def close_dialogs(self) -> None:
        """Stop what runs behind the settings page (the window is closing)."""
        self._services.tiles.shutdown()

    def release(self) -> None:
        """Release the tile cache (the data thread and the web engine are already stopped)."""
        self._services.release()
