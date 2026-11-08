# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controller of the map: the filter, the visible area and the selection become jobs and signals.

It lives on the UI thread and decides what to ask the data thread (`MapQueryHost`) when the filter
changes, the map moves or an activity is selected; the answers come back as signals that the map
page, the list and the table show. One search and one area query at a time: while one runs, a newer
request replaces the one waiting (a fast drag asks for the last view only). Answers about an
outdated filter, view or selection are not shown. Positions are personal data: no log.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import QObject, Signal

from ponos_coach.activities.domain import ActivityFilter, ActivityIdentity, identity_key
from ponos_coach.activities.map_queries import SearchResult
from ponos_coach.ui.activity_filters import SharedFilterState
from ponos_coach.ui.data_thread import JobFailed, JobOutcome, JobSucceeded
from ponos_coach.ui.map_messages import MapView, index_by_identity, parse_activity_index
from ponos_coach.ui.map_query_host import AreaPage, MapQueryHost, ProfilePage, SearchPage
from ponos_coach.ui.overview_state import Idle
from ponos_coach.ui.workspace import ProfileAccess, WorkspaceController


class MapController(QObject):
    """Search, area view and profile of the selected activity, for the map and the table."""

    search_changed = Signal(object)  # SearchResult: the activities that pass the filter
    area_changed = Signal(object)  # AreaView: starts, totals and tracks of the visible area
    profile_changed = Signal(object)  # ProfileOutcome | None (None: nothing selected)
    selection_changed = Signal(object, bool)  # ActivityIdentity | None, bring it into view
    profile_opened = Signal()  # a profile was opened: the map should frame its activities
    failed = Signal(object)  # ErrorMessage

    def __init__(
        self,
        workspace: WorkspaceController,
        host: MapQueryHost,
        filters: SharedFilterState,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._workspace = workspace
        self._host = host
        self._filters = filters
        self._search: SearchResult | None = None
        self._index_of: dict[str, int] = {}
        self._view: MapView | None = None
        self._selected: ActivityIdentity | None = None
        self._searching = False
        self._querying_area = False
        self._search_asked: ActivityFilter | None = None
        self._area_asked: tuple[ActivityFilter, MapView, int] | None = None
        # Numbers of the lists the page was given: every message the page sends back names the
        # one it refers to, and a message about an older list is dropped.
        self._counter = 0
        self._list_generation = 0
        self._profile_generation = 0
        self._starts_script: str | None = None
        self._tracks_script: str | None = None
        self._profile_script: str | None = None
        filters.changed.connect(self.refresh_search)
        workspace.profile_changed.connect(self._on_profile_changed)
        workspace.overview_changed.connect(self._on_overview_changed)

    # --- State -------------------------------------------------------------------------

    @property
    def search(self) -> SearchResult | None:
        """The last search result (the activities that pass the filter), if any."""
        return self._search

    @property
    def view(self) -> MapView | None:
        """The area the map reported last."""
        return self._view

    @property
    def selected(self) -> ActivityIdentity | None:
        """The selected activity."""
        return self._selected

    @property
    def list_generation(self) -> int:
        """Number of the list (search result) the page's indexes refer to now."""
        return self._list_generation

    @property
    def profile_generation(self) -> int:
        """Number of the profile points the page holds."""
        return self._profile_generation

    @property
    def starts_script(self) -> str | None:
        """JavaScript that shows the starts of the last search (built on the data thread)."""
        return self._starts_script

    @property
    def tracks_script(self) -> str | None:
        """JavaScript that shows the tracks of the last area (built on the data thread)."""
        return self._tracks_script

    @property
    def profile_script(self) -> str | None:
        """JavaScript that sends the points of the selected profile, if it has any."""
        return self._profile_script

    def _take_generation(self) -> int:
        self._counter += 1
        return self._counter

    def index_map(self) -> Mapping[str, int]:
        """Position in the last search result of every activity, by identity key (read-only)."""
        return self._index_of

    def index_of(self, identity: ActivityIdentity) -> int | None:
        """Position of an activity in the last search result (what the page calls it)."""
        return self._index_of.get(identity_key(identity))

    # --- Requests from the views -------------------------------------------------------

    def set_view(self, view: MapView) -> None:
        """The map moved or zoomed: ask for the activities and tracks of the new area."""
        if view != self._view:
            self._view = view
            self._refresh_area()

    def refresh_search(self) -> None:
        """Search again with the current filter (it also notices new or changed activities)."""
        if self._searching:
            return  # the answer will notice that the filter changed and ask again
        criteria = self._filters.criteria
        host = self._host
        self._search_asked = criteria
        generation = self._take_generation()

        def work(access: ProfileAccess) -> object:
            return host.search_page(access, criteria, generation)

        self._searching = self._workspace.run_on_profile(work, self._on_search_done)

    def select_identity(self, identity: ActivityIdentity | None, *, reveal: bool = False) -> None:
        """Select an activity (or none) from anywhere: the table, the list or the map."""
        if identity == self._selected:
            return
        self._selected = identity
        self.selection_changed.emit(identity, reveal)
        self._request_profile(identity)

    def select_index(self, position: object) -> None:
        """Select the activity at a position of the last search result, as the page names it.

        A position outside the list (a message that is not ours) changes nothing.
        """
        search = self._search
        index = parse_activity_index(position, 0 if search is None else len(search.activities))
        if search is not None and index is not None:
            self.select_identity(search.activities[index].identity)

    # --- Reactions of the workspace --------------------------------------------------------

    def _on_profile_changed(self, _view: object) -> None:
        self._reset_list()
        self._profile_script = None
        self._selected = None
        self.profile_opened.emit()
        self.selection_changed.emit(None, False)
        self.profile_changed.emit(None)

        def work(access: ProfileAccess) -> object:
            return self._host.backfill(access)

        self._workspace.run_on_profile(work, self._after_backfill)

    def _reset_list(self) -> None:
        self._search = None
        self._index_of = {}
        self._starts_script = None
        self._tracks_script = None
        self._list_generation = self._take_generation()

    def _after_backfill(self, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobFailed):  # derived data: the map still works with what exists
            self.failed.emit(self._workspace.describe_failure(outcome.error))
        self.refresh_search()

    def _on_overview_changed(self, state: object) -> None:
        if isinstance(state, Idle):
            self._reset_list()
            self.search_changed.emit(None)
        else:
            self.refresh_search()

    # --- Jobs -----------------------------------------------------------------------------

    def _on_search_done(self, outcome: JobOutcome) -> None:
        self._searching = False
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, SearchPage):
            self._adopt(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._workspace.describe_failure(outcome.error))
        if self._search_asked != self._filters.criteria:
            self.refresh_search()  # the filter changed while the search ran

    def _adopt(self, page: SearchPage) -> None:
        result = page.result
        self._search = result
        self._list_generation = page.generation
        self._starts_script = page.script
        self._index_of = index_by_identity(result)
        if self._selected is not None and identity_key(self._selected) not in self._index_of:
            self.select_identity(None)
        self.search_changed.emit(result)
        self._refresh_area()

    def _refresh_area(self) -> None:
        view = self._view
        if view is None or self._search is None or self._querying_area:
            return
        criteria = self._filters.criteria
        host = self._host
        index_of = self._index_of  # replaced, never changed in place: safe on the data thread
        generation = self._list_generation
        self._area_asked = (criteria, view, generation)

        def work(access: ProfileAccess) -> object:
            return host.area_page(access, criteria, view, index_of, generation)

        self._querying_area = self._workspace.run_on_profile(work, self._on_area_done)

    def _on_area_done(self, outcome: JobOutcome) -> None:
        self._querying_area = False
        if (
            isinstance(outcome, JobSucceeded)
            and isinstance(outcome.value, AreaPage)
            and outcome.value.generation == self._list_generation
        ):  # tracks numbered with an older list would name the wrong activities
            self._tracks_script = outcome.value.script
            self.area_changed.emit(outcome.value.view)
        elif isinstance(outcome, JobFailed):
            self.failed.emit(self._workspace.describe_failure(outcome.error))
        if self._area_asked != (self._filters.criteria, self._view, self._list_generation):
            self._refresh_area()  # the map moved (or the filter changed) while the query ran

    def _request_profile(self, identity: ActivityIdentity | None) -> None:
        if identity is None:
            self._profile_script = None
            self.profile_changed.emit(None)
            return
        host = self._host
        generation = self._take_generation()

        def work(access: ProfileAccess) -> object:
            return host.profile_page(access, identity, generation)

        def done(outcome: JobOutcome) -> None:
            if identity != self._selected:
                return  # another activity was selected meanwhile
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, ProfilePage):
                self._profile_generation = outcome.value.generation
                self._profile_script = outcome.value.script
                self.profile_changed.emit(outcome.value.outcome)
            elif isinstance(outcome, JobFailed):
                self.failed.emit(self._workspace.describe_failure(outcome.error))

        self._workspace.run_on_profile(work, done)
