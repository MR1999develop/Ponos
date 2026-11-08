# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Map use cases: search, area view with tracks, track boxes and their resumable fill.

T-MAP-02 (list = filtered activities starting in the area), T-MAP-03 (antimeridian, extreme zooms,
many activities in one place, none), T-MAP-04 (no position: counted apart), T-MAP-07 (the tracks
that are drawn), all with the in-memory fake of the profile.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    MAX_TRACK_ZOOM,
    ActivityFilter,
    ActivityRecord,
    ActivitySummary,
    ActivityTrack,
    FileFormat,
    GeoBox,
    GeoPoint,
    NumericRange,
    Sport,
    TrackSegment,
    encode_polyline,
    track_box_of,
)
from ponos_coach.activities.map_queries import (
    MAX_MAP_TRACKS,
    TRACK_MIN_ZOOM,
    ActivitiesInArea,
    ActivitySnapshots,
    SearchActivities,
    backfill_track_boxes,
)
from ponos_coach.activities.ports import TrackBoxIndex
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import T0, absolute_path, fit_identity, make_record
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryProfileStorage

ROME = ZoneInfo("Europe/Rome")
EVERYWHERE = GeoBox(-90.0, -180.0, 90.0, 180.0)


def summary_at(lat: float, lon: float, *, length: int = 3) -> ActivitySummary:
    """A summary that starts at the point and runs a few hundred metres north-east."""
    points = [GeoPoint(lat + i * 0.002, lon + i * 0.002) for i in range(length)]
    return ActivitySummary(
        distance_m=5000.0,
        start_point=points[0],
        track_polyline=encode_polyline(points),
    )


def new_profile() -> OpenProfile:
    return InMemoryProfileStorage().create(
        absolute_path("p"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "Europe/Rome",
    )


class World:
    """A fake profile, its archive and the three map use cases on top of it."""

    def __init__(self, decode_track=None) -> None:  # type: ignore[no-untyped-def]  # noqa: ANN001
        self.profile = new_profile()
        self.archive = InMemoryActivityArchive()
        self.number = 0
        self.snapshots = ActivitySnapshots(self.profile.track_boxes, ROME)
        self.search = SearchActivities(self.snapshots)
        self.decode_calls = 0
        self._decode_track = decode_track or self._no_file
        self.in_area = ActivitiesInArea(self.snapshots, self.archive, self._decode)

    def _no_file(self, _data: bytes, _file_format: FileFormat) -> ActivityTrack | None:
        raise ActivityFileError(ActivityFileReason.CORRUPT)

    def _decode(self, data: bytes, file_format: FileFormat) -> ActivityTrack | None:
        self.decode_calls += 1
        return self._decode_track(data, file_format)

    def add(
        self,
        summary: ActivitySummary | None,
        *,
        days: int = 0,
        sport: Sport = Sport.RUNNING,
        file: bytes | None = None,
    ) -> ActivityRecord:
        """Adds an activity; with `file` its original is really in the archive."""
        self.number += 1
        record = make_record(
            fit_identity(self.number),
            start=T0 + timedelta(days=days),
            sport=sport,
            summary=summary,
        )
        if file is not None:
            archived = self.archive.store(file, FileFormat.FIT, record.start)
            record = replace(record, archived=archived)
        self.profile.activities.add(record)
        return record


def test_t_map_04_activities_without_position_are_counted_apart_and_not_on_the_map() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0))
    world.add(ActivitySummary(distance_m=20_000.0), days=1)  # indoor bike

    result = world.search(ActivityFilter())
    view = world.in_area(ActivityFilter(), EVERYWHERE, 5)

    assert len(result.activities) == 2
    assert result.without_position == 1
    assert result.start_indices.tolist() == [0]
    assert view.in_area.without_position == 1
    assert len(view.in_area.activities) == 1


def test_search_returns_the_filtered_activities_with_totals_and_start_columns() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0), sport=Sport.RUNNING)
    world.add(summary_at(46.0, 10.0), days=1, sport=Sport.CYCLING)

    result = world.search(ActivityFilter(sports=frozenset({Sport.CYCLING})))

    assert [r.sport for r in result.activities] == [Sport.CYCLING]
    assert result.totals.count == 1
    assert result.totals.distance_km == 5.0
    assert result.start_lat_deg.tolist() == [46.0]
    assert result.start_lon_deg.tolist() == [10.0]
    assert not result.start_lat_deg.flags.writeable


def test_an_empty_profile_gives_empty_results_at_every_zoom() -> None:
    world = World()

    assert world.search(ActivityFilter()).activities == ()
    for zoom in (0, TRACK_MIN_ZOOM, MAX_TRACK_ZOOM):
        view = world.in_area(ActivityFilter(), EVERYWHERE, zoom)
        assert view.in_area.activities == ()
        assert view.tracks == ()
        assert view.in_area.totals.count == 0


def test_t_map_03_an_area_across_the_antimeridian_finds_the_starts_on_both_sides() -> None:
    world = World()
    world.add(summary_at(-17.0, 179.9))
    world.add(summary_at(-17.0, -179.9), days=1)
    world.add(summary_at(-17.0, 0.0), days=2)

    view = world.in_area(ActivityFilter(), GeoBox(-20.0, 175.0, -10.0, -175.0), 6)

    assert len(view.in_area.activities) == 2


def test_t_map_03_many_activities_at_the_same_point_are_all_listed() -> None:
    world = World()
    for day in range(120):
        world.add(summary_at(45.0, 9.0), days=day)

    view = world.in_area(ActivityFilter(), GeoBox(44.9, 8.9, 45.1, 9.1), 12)

    assert len(view.in_area.activities) == 120
    assert view.in_area.totals.count == 120


def test_the_area_view_respects_the_filter() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0))
    world.add(ActivitySummary(distance_m=40_000.0, start_point=GeoPoint(45.0, 9.0)), days=1)
    criteria = ActivityFilter(distance_m=NumericRange(30_000.0, None))

    view = world.in_area(criteria, EVERYWHERE, 3)

    assert [r.summary.distance_m for r in view.in_area.activities] == [40_000.0]


def test_no_tracks_below_the_track_zoom() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0))

    assert world.in_area(ActivityFilter(), EVERYWHERE, TRACK_MIN_ZOOM - 1).tracks == ()
    assert len(world.in_area(ActivityFilter(), EVERYWHERE, TRACK_MIN_ZOOM).tracks) == 1


def test_a_track_that_only_passes_through_the_area_is_drawn_but_not_listed() -> None:
    world = World()
    long_track = [GeoPoint(45.0, 9.0 + i * 0.01) for i in range(11)]  # 10 km west to east
    world.add(
        ActivitySummary(start_point=long_track[0], track_polyline=encode_polyline(long_track))
    )

    view = world.in_area(ActivityFilter(), GeoBox(44.9, 9.04, 45.1, 9.06), 12)

    assert view.in_area.activities == ()  # it does not start there
    assert len(view.tracks) == 1


def test_t_map_07_only_the_most_recent_tracks_are_sent_and_the_rest_counted() -> None:
    world = World()
    for day in range(MAX_MAP_TRACKS + 25):
        world.add(summary_at(45.0, 9.0), days=day)

    view = world.in_area(ActivityFilter(), EVERYWHERE, 12)

    assert len(view.tracks) == MAX_MAP_TRACKS
    assert view.tracks_omitted == 25
    newest = max(world.profile.activities.list_by_start(), key=lambda r: r.start)
    assert newest.identity in {t.identity for t in view.tracks}
    oldest = min(world.profile.activities.list_by_start(), key=lambda r: r.start)
    assert oldest.identity not in {t.identity for t in view.tracks}


def test_t_map_07_a_track_crossing_the_antimeridian_is_cut_where_it_jumps() -> None:
    world = World()
    points = [GeoPoint(-17.0, 179.99), GeoPoint(-17.0, 179.995), GeoPoint(-17.0, -179.995),
              GeoPoint(-17.0, -179.99)]  # fmt: skip
    world.add(ActivitySummary(start_point=points[0], track_polyline=encode_polyline(points)))

    (track,) = world.in_area(ActivityFilter(), GeoBox(-20.0, 175.0, -10.0, -175.0), 12).tracks

    assert len(track.segments) == 2
    for segment in track.segments:
        longitudes = [p.lon_deg for p in segment]
        assert max(longitudes) - min(longitudes) < 1.0


def test_t_map_07_at_a_fine_zoom_the_file_is_read_and_its_gaps_are_kept() -> None:
    first = TrackSegment(
        np.array([0.0, 10.0, 20.0]),
        np.array([45.0, 45.0005, 45.001]),
        np.array([9.0, 9.0005, 9.001]),
    )
    second = TrackSegment(
        np.array([100.0, 110.0]), np.array([45.01, 45.0105]), np.array([9.01, 9.0105])
    )
    track = ActivityTrack((first, second))
    world = World(decode_track=lambda _data, _format: track)
    world.add(summary_at(45.0, 9.0), file=b"any bytes: the decoder is a stub")

    view = world.in_area(ActivityFilter(), EVERYWHERE, 18)

    (drawn,) = view.tracks
    assert len(drawn.segments) == 2  # the gap of the file is not bridged
    assert world.decode_calls == 1


def test_a_file_that_cannot_be_used_falls_back_to_the_index_polyline() -> None:
    world = World()  # the decoder always fails
    world.add(summary_at(45.0, 9.0, length=5))

    view = world.in_area(ActivityFilter(), EVERYWHERE, 18)

    (drawn,) = view.tracks
    assert sum(len(s) for s in drawn.segments) >= 2


def test_file_tracks_are_cached_per_zoom() -> None:
    track = ActivityTrack(
        (TrackSegment(np.array([0.0, 5.0]), np.array([45.0, 45.001]), np.array([9.0, 9.001])),)
    )
    world = World(decode_track=lambda _data, _format: track)
    world.add(summary_at(45.0, 9.0), file=b"stub")

    world.in_area(ActivityFilter(), EVERYWHERE, 18)
    world.in_area(ActivityFilter(), EVERYWHERE, 18)

    assert world.decode_calls == 1


def test_the_snapshot_notices_a_new_activity_without_being_told() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0))
    assert len(world.search(ActivityFilter()).activities) == 1

    world.add(summary_at(46.0, 10.0), days=1)

    assert len(world.search(ActivityFilter()).activities) == 2


def test_the_zoom_is_clamped_instead_of_rejected() -> None:
    world = World()
    world.add(summary_at(45.0, 9.0))

    assert len(world.in_area(ActivityFilter(), EVERYWHERE, 99).tracks) == 1
    assert world.in_area(ActivityFilter(), EVERYWHERE, -4).tracks == ()


# --- T-MAP-02: the list is the filtered activities that start in the area ---------------

latitudes = st.floats(min_value=-80.0, max_value=80.0)
longitudes = st.floats(min_value=-180.0, max_value=180.0)


@settings(max_examples=40, deadline=None)
@given(
    starts=st.lists(st.tuples(latitudes, longitudes), min_size=0, max_size=25),
    area_lat=st.tuples(latitudes, latitudes),
    area_lon=st.tuples(longitudes, longitudes),
    minimum_km=st.floats(min_value=0.0, max_value=8.0),
)
def test_t_map_02_property_the_list_equals_a_naive_filter_and_scan(
    starts: list[tuple[float, float]],
    area_lat: tuple[float, float],
    area_lon: tuple[float, float],
    minimum_km: float,
) -> None:
    world = World()
    for i, (lat, lon) in enumerate(starts):
        summary = ActivitySummary(distance_m=float(i % 10) * 1000.0, start_point=GeoPoint(lat, lon))
        world.add(summary, days=i)
    south, north = sorted(area_lat)
    west, east = area_lon  # west > east is an area across the antimeridian
    area = GeoBox(south, west, north, east)
    criteria = ActivityFilter(distance_m=NumericRange(minimum_km * 1000.0, None))

    view = world.in_area(criteria, area, 6)

    expected = [
        r
        for r in world.profile.activities.list_by_start()
        if criteria.matches(r, ROME)
        and r.summary.start_point is not None
        and _naive_inside(area, r.summary.start_point)
    ]
    assert list(view.in_area.activities) == expected
    assert view.in_area.totals.count == len(expected)


def _naive_inside(area: GeoBox, point: GeoPoint) -> bool:
    if not area.south <= point.lat_deg <= area.north:
        return False
    lon = point.lon_deg
    if area.west <= area.east:
        return area.west <= lon <= area.east or {lon, area.west} == {-180.0, 180.0}
    return lon >= area.west or lon <= area.east


# --- Boxes: intersection against a naive version on the circle ----------------------------


def _naive_intersects(a: GeoBox, b: GeoBox) -> bool:
    """Intervals on the real line with copies shifted by whole turns."""
    if a.south > b.north or b.south > a.north:
        return False
    a_low, a_high = a.west, a.east + (360.0 if a.west > a.east else 0.0)
    b_low, b_high = b.west, b.east + (360.0 if b.west > b.east else 0.0)
    return any(
        a_low <= b_high + shift and b_low + shift <= a_high for shift in (-360.0, 0.0, 360.0)
    )


boxes = st.builds(
    lambda lats, west, east: GeoBox(min(lats), west, max(lats), east),
    st.tuples(latitudes, latitudes),
    st.integers(-358, 358).map(lambda n: n / 2.0),  # exact in binary: the naive sum has no noise
    st.integers(-358, 358).map(lambda n: n / 2.0),
)


@settings(max_examples=300, deadline=None)
@given(a=boxes, b=boxes)
def test_t_map_03_box_intersection_matches_the_naive_one_on_the_circle(
    a: GeoBox, b: GeoBox
) -> None:
    assert a.intersects(b) == _naive_intersects(a, b)
    assert a.intersects(b) == b.intersects(a)


def test_the_box_of_an_indexed_track_covers_start_and_polyline() -> None:
    start = GeoPoint(45.0, 9.0)
    polyline = encode_polyline([start, GeoPoint(45.5, 9.2), GeoPoint(45.2, 8.8)])

    box = track_box_of(start, polyline)

    assert box == GeoBox(45.0, 8.8, 45.5, 9.2)
    assert track_box_of(None, None) is None
    assert track_box_of(start, None) == GeoBox(45.0, 9.0, 45.0, 9.0)


# --- Resumable fill of the boxes -----------------------------------------------------------


def _blank_boxes(profile: OpenProfile) -> None:
    """Simulate a profile written before v8: the boxes are missing."""
    profile.track_boxes._db.track_boxes.clear()  # type: ignore[attr-defined]


def test_the_fill_gives_every_located_activity_its_box_and_is_idempotent() -> None:
    world = World()
    for day in range(7):
        world.add(summary_at(45.0 + day, 9.0), days=day)
    world.add(ActivitySummary(distance_m=1000.0), days=9)  # no position: never gets a box
    _blank_boxes(world.profile)
    index: TrackBoxIndex = world.profile.track_boxes
    assert index.count_without_box() == 7

    done = backfill_track_boxes(index, batch_size=3)

    assert (done.filled, done.unresolved, done.cancelled) == (7, 0, False)
    assert index.count_without_box() == 0
    again = backfill_track_boxes(index, batch_size=3)
    assert (again.filled, again.cancelled) == (0, False)


def test_a_cancelled_fill_keeps_what_it_did_and_the_next_one_carries_on() -> None:
    world = World()
    for day in range(10):
        world.add(summary_at(45.0 + day, 9.0), days=day)
    _blank_boxes(world.profile)
    index = world.profile.track_boxes
    batches = 0

    def cancel_after_two() -> bool:
        nonlocal batches
        batches += 1
        return batches > 2

    first = backfill_track_boxes(index, batch_size=3, is_cancelled=cancel_after_two)

    assert first.cancelled
    assert first.filled == 6
    assert index.count_without_box() == 4
    second = backfill_track_boxes(index, batch_size=3)
    assert (second.filled, second.cancelled) == (4, False)
    assert index.count_without_box() == 0


def test_the_fill_reports_progress_from_zero_to_the_total() -> None:
    world = World()
    for day in range(5):
        world.add(summary_at(45.0, 9.0), days=day)
    _blank_boxes(world.profile)
    seen: list[tuple[int, int]] = []

    backfill_track_boxes(
        world.profile.track_boxes, batch_size=2, report=lambda d, t: seen.append((d, t))
    )

    assert seen == [(0, 5), (2, 5), (4, 5), (5, 5)]


def test_a_damaged_polyline_falls_back_to_the_start_point() -> None:
    world = World()
    world.add(ActivitySummary(start_point=GeoPoint(45.0, 9.0), track_polyline="_"), days=0)
    _blank_boxes(world.profile)

    done = backfill_track_boxes(world.profile.track_boxes)

    assert done.filled == 1
    (item,) = world.profile.track_boxes.list_by_start()
    assert item.track_box == GeoBox(45.0, 9.0, 45.0, 9.0)


def test_the_fill_rejects_a_batch_size_below_one() -> None:
    with pytest.raises(ValueError, match="batch"):
        backfill_track_boxes(new_profile().track_boxes, batch_size=0)


def test_activities_added_after_the_fact_get_their_box_at_once() -> None:
    world = World()

    world.add(summary_at(45.0, 9.0))

    assert world.profile.track_boxes.count_without_box() == 0
    (item,) = world.profile.track_boxes.list_by_start()
    assert item.track_box is not None
    assert item.track_box.contains(45.0, 9.0)
