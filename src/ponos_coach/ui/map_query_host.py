# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map's use cases, bound to the open profile and run on the data thread.

`ActivitySnapshots` reads the index once per revision and keeps the last selection per filter, so
it must live where the profile's connection lives: the data thread. The host is created by the
Composition Root, used only inside jobs (`WorkspaceController.run_on_profile`) and rebuilds its
bindings when the profile changes. Positions are personal data: nothing here logs them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ponos_coach.activities.domain import ActivityFilter, ActivityIdentity
from ponos_coach.activities.map_queries import (
    ActivitiesInArea,
    ActivitySnapshots,
    AreaView,
    BackfillSummary,
    SearchActivities,
    SearchResult,
    TrackDecoder,
    backfill_track_boxes,
)
from ponos_coach.load.activity_profile import ActivityProfileData, GetActivityProfile, NoTrack
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.profiles.use_cases import ProfileSession
from ponos_coach.ui.map_messages import (
    MapView,
    page_call,
    profile_message,
    starts_message,
    tracks_message,
)
from ponos_coach.ui.workspace import ProfileAccess

type ProfileOutcome = ActivityProfileData | NoTrack | ExcludedActivity


@dataclass(frozen=True, slots=True)
class SearchPage:
    """A search result and the script that shows its starts, built on the data thread."""

    result: SearchResult
    generation: int
    script: str


@dataclass(frozen=True, slots=True)
class AreaPage:
    """An area view and the script that shows its tracks, built on the data thread."""

    view: AreaView
    generation: int
    script: str


@dataclass(frozen=True, slots=True)
class ProfilePage:
    """The outcome for an activity and, if it has a profile, the script that sends its points."""

    outcome: ProfileOutcome
    generation: int
    script: str | None


@dataclass(frozen=True, slots=True)
class _Bound:
    session: ProfileSession
    search: SearchActivities
    area: ActivitiesInArea


class MapQueryHost:
    """Search, area view, activity profile and box fill for the profile that is open."""

    def __init__(self, decode_track: TrackDecoder) -> None:
        self._decode_track = decode_track
        self._bound: _Bound | None = None

    def search(self, access: ProfileAccess, criteria: ActivityFilter) -> SearchResult:
        """The activities that pass the filter (an empty filter gives all of them)."""
        return self._for(access).search(criteria)

    def search_page(
        self, access: ProfileAccess, criteria: ActivityFilter, generation: int
    ) -> SearchPage:
        """`search` plus the JSON for the page: the UI thread only hands the string over."""
        result = self.search(access, criteria)
        script = page_call("setStarts", starts_message(result, generation))
        return SearchPage(result, generation, script)

    def area_page(
        self,
        access: ProfileAccess,
        criteria: ActivityFilter,
        view: MapView,
        index_of: Mapping[str, int],
        generation: int,
    ) -> AreaPage:
        """`area` plus the JSON of its tracks, numbered with the list they refer to."""
        area = self.area(access, criteria, view)
        script = page_call("setTracks", tracks_message(area.tracks, index_of, generation))
        return AreaPage(area, generation, script)

    def profile_page(
        self, access: ProfileAccess, identity: ActivityIdentity, generation: int
    ) -> ProfilePage:
        """`profile` plus the JSON of its points (only for an activity that has a profile)."""
        outcome = self.profile(access, identity)
        script = (
            page_call("setProfile", profile_message(outcome, generation))
            if isinstance(outcome, ActivityProfileData)
            else None
        )
        return ProfilePage(outcome, generation, script)

    def area(self, access: ProfileAccess, criteria: ActivityFilter, view: MapView) -> AreaView:
        """Starts in the area, their totals and, from zoom 11, the tracks."""
        return self._for(access).area(criteria, view.box, view.zoom)

    def profile(self, access: ProfileAccess, identity: ActivityIdentity) -> ProfileOutcome:
        """Altitude over distance and HR zones of one activity, or why there is no profile."""
        use_case = GetActivityProfile(
            access.session.activities,
            access.archive,
            access.session.physiology,
            access.decode,
            self._decode_track,
            timezone=access.zone,
        )
        return use_case(identity)

    def backfill(self, access: ProfileAccess) -> BackfillSummary:
        """Fills the track boxes of activities indexed before they existed (resumable: closing
        the app stops it between two batches and the next start carries on)."""
        return backfill_track_boxes(
            access.session.track_boxes, is_cancelled=access.context.is_cancelled
        )

    def _for(self, access: ProfileAccess) -> _Bound:
        bound = self._bound
        if bound is None or bound.session is not access.session:
            snapshots = ActivitySnapshots(access.session.track_boxes, access.zone)
            bound = _Bound(
                access.session,
                SearchActivities(snapshots),
                ActivitiesInArea(snapshots, access.archive, self._decode_track),
            )
            self._bound = bound
        return bound
