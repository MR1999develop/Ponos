# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 5b acceptance campaign, map queries: T-MAP-03, T-MAP-05, T-MAP-07 and resumable fill.

Synthetic activities only (invented athlete, positions drawn by a seeded generator over invented
grids). The timings are seconds from a monotonic clock, reported as a distribution (p50, p95,
worst); the budgets are the ones of PLAN.md 7.11.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
import sys
from collections.abc import Callable
from functools import partial
from time import perf_counter

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    ActivityFilter,
    ActivitySummary,
    ActivityTrack,
    GeoBox,
    GeoPoint,
    Sport,
    TrackSegment,
    encode_polyline,
    simplify_for_zoom,
    zoom_tolerance_m,
)
from ponos_coach.activities.map_queries import (
    MAX_MAP_TRACKS,
    TRACK_MIN_ZOOM,
    backfill_track_boxes,
)
from tests.activities.test_map_queries import World, summary_at
from tests.builders import fit_identity

TOTAL = 50_000
SEED = 20261108
SEARCH_BUDGET_S = 0.4  # T-MAP-05, p95
FIRST_DRAW_BUDGET_S = 2.0  # T-MAP-07
PAN_BUDGET_S = 0.4  # T-MAP-07, p95
VIEWS = 40


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1)]


def describe(values: list[float]) -> str:
    return (
        f"p50 {statistics.median(values) * 1000:.1f} ms, "
        f"p95 {percentile(values, 0.95) * 1000:.1f} ms, worst {max(values) * 1000:.1f} ms"
    )


def timed(action: Callable[[], object]) -> float:
    started = perf_counter()
    action()
    return perf_counter() - started


@pytest.fixture(scope="module")
def big_world() -> World:
    """50,000 activities in 12 invented clusters, one per day, one in fifty without position."""
    rng = random.Random(SEED)
    centres = [(rng.uniform(-50, 60), rng.uniform(-170, 170)) for _ in range(12)]
    world = World()
    for i in range(TOTAL):
        if i % 50 == 0:
            world.add(ActivitySummary(distance_m=20_000.0), days=i)  # indoor: no position
            continue
        lat, lon = centres[i % len(centres)]
        world.add(
            summary_at(lat + rng.uniform(-0.4, 0.4), lon + rng.uniform(-0.4, 0.4), length=12),
            days=i,
            sport=Sport.CYCLING if i % 3 == 0 else Sport.RUNNING,
        )
    return world


def cluster_centres(world: World) -> list[tuple[float, float]]:
    selection = world.search(ActivityFilter())
    return [
        (float(selection.start_lat_deg[i]), float(selection.start_lon_deg[i]))
        for i in range(0, selection.start_lat_deg.size, 997)
    ]


def view_around(rng: random.Random, zoom: int, centres: list[tuple[float, float]]) -> GeoBox:
    lat, lon = rng.choice(centres)
    span = 180.0 / 2**zoom * 4
    return GeoBox(
        max(-90.0, lat - span),
        max(-180.0, lon - span),
        min(90.0, lat + span),
        min(180.0, lon + span),
    )


def test_t_map_05_area_list_with_50000_activities_stays_within_budget(big_world: World) -> None:
    centres = cluster_centres(big_world)
    rng = random.Random(SEED)
    cold = timed(lambda: big_world.search(ActivityFilter()))
    durations = []
    for _ in range(VIEWS):
        zoom = rng.choice([3, 5, 8, 10])
        area = view_around(rng, zoom, centres)
        durations.append(timed(partial(big_world.in_area, ActivityFilter(), area, zoom)))
    whole = GeoBox(-90.0, -180.0, 90.0, 180.0)
    whole_world = timed(lambda: big_world.in_area(ActivityFilter(), whole, 2))
    sys.stdout.write(
        f"[T-MAP-05] cold search {cold * 1000:.0f} ms; area list {describe(durations)}; "
        f"whole world (50,000 rows) {whole_world * 1000:.0f} ms\n"
    )
    assert whole_world < SEARCH_BUDGET_S

    assert percentile(durations, 0.95) < SEARCH_BUDGET_S
    assert big_world.search(ActivityFilter()).without_position == TOTAL // 50


def test_t_map_05_a_filter_change_with_50000_activities_is_measured(big_world: World) -> None:
    """The filter change rebuilds the selection (the heavy step): measured and loosely bounded."""
    big_world.search(ActivityFilter())
    elapsed = timed(lambda: big_world.search(ActivityFilter(sports=frozenset({Sport.CYCLING}))))
    sys.stdout.write(f"[T-MAP-05] filter change (sport): {elapsed * 1000:.0f} ms\n")

    assert elapsed < 2.0  # not in the plan: guards against an order-of-magnitude regression


def test_t_map_07_first_draw_and_pan_with_50000_tracks(big_world: World) -> None:
    centres = cluster_centres(big_world)
    rng = random.Random(SEED + 1)
    first = timed(lambda: big_world.in_area(ActivityFilter(), view_around(rng, 11, centres), 12))
    pans = []
    for _ in range(VIEWS):
        zoom = rng.choice([11, 12, 13])
        view = view_around(rng, zoom, centres)
        pans.append(timed(partial(big_world.in_area, ActivityFilter(), view, zoom)))
    sys.stdout.write(f"[T-MAP-07] first draw {first * 1000:.0f} ms; pan {describe(pans)}\n")

    assert first < FIRST_DRAW_BUDGET_S
    assert percentile(pans, 0.95) < PAN_BUDGET_S


def test_t_map_07_tracks_in_an_answer_are_capped_and_absent_below_zoom_11(
    big_world: World,
) -> None:
    area = GeoBox(-90.0, -180.0, 90.0, 180.0)
    view = big_world.in_area(ActivityFilter(), area, TRACK_MIN_ZOOM)

    assert len(view.tracks) <= MAX_MAP_TRACKS
    assert view.tracks_omitted + len(view.tracks) >= MAX_MAP_TRACKS
    assert not big_world.in_area(ActivityFilter(), area, TRACK_MIN_ZOOM - 1).tracks


# --- T-MAP-03: antimeridian against a naive filter -------------------------------------------


def naive_inside(box: GeoBox, lat: float, lon: float) -> bool:
    if not box.south <= lat <= box.north:
        return False
    for value in (lon, lon + 360.0, lon - 360.0):
        if box.west > box.east:
            if box.west <= value <= box.east + 360.0:
                return True
        elif box.west <= value <= box.east:
            return True
    return False


@settings(max_examples=60, deadline=None, derandomize=True)
@given(
    south=st.floats(-80, 79),
    height=st.floats(0.1, 10),
    west=st.floats(-180, 180),
    width=st.floats(0.1, 40),
    points=st.lists(
        st.tuples(
            st.floats(-80, 80),
            st.sampled_from([-180.0, 180.0, 179.9, -179.9, 175.0, -175.0, 0.0]),
        ),
        min_size=1,
        max_size=25,
    ),
)
def test_t_map_03_area_list_across_the_antimeridian_equals_a_naive_filter(
    south: float, height: float, west: float, width: float, points: list[tuple[float, float]]
) -> None:
    east = west + width
    east = east - 360.0 if east > 180.0 else east
    area = GeoBox(south, west, min(90.0, south + height), east)
    world = World()
    for i, (lat, lon) in enumerate(points):
        world.add(ActivitySummary(distance_m=1000.0, start_point=GeoPoint(lat, lon)), days=i)

    found = {r.identity for r in world.in_area(ActivityFilter(), area, 5).in_area.activities}
    expected = {
        fit_identity(i + 1) for i, (lat, lon) in enumerate(points) if naive_inside(area, lat, lon)
    }

    assert found == expected


def test_t_map_03_a_track_that_crosses_the_antimeridian_is_cut_not_drawn_across_the_world() -> None:
    points = [
        GeoPoint(10.0, 179.97),
        GeoPoint(10.01, 179.99),
        GeoPoint(10.02, -179.99),
        GeoPoint(10.03, -179.97),
    ]
    world = World()
    world.add(
        ActivitySummary(
            distance_m=5000.0, start_point=points[0], track_polyline=encode_polyline(points)
        )
    )

    for area in (
        GeoBox(9.9, 179.9, 10.1, 180.0),
        GeoBox(9.9, -180.0, 10.1, -179.9),
        GeoBox(9.9, 179.9, 10.1, -179.9),
    ):
        view = world.in_area(ActivityFilter(), area, 14)
        assert len(view.tracks) == 1
        for segment in view.tracks[0].segments:
            lons = [p.lon_deg for p in segment]
            assert max(lons) - min(lons) < 180.0


# --- T-MAP-07: simplification error against the full track ----------------------------------


def distance_to_polyline_m(point: GeoPoint, line: tuple[GeoPoint, ...]) -> float:
    lat0 = math.radians(point.lat_deg)
    kx, ky = 111_320.0 * math.cos(lat0), 110_540.0
    px, py = point.lon_deg * kx, point.lat_deg * ky
    best = math.inf
    for a, b in itertools.pairwise(line):
        ax, ay, bx, by = a.lon_deg * kx, a.lat_deg * ky, b.lon_deg * kx, b.lat_deg * ky
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
        best = min(best, math.hypot(px - ax - t * dx, py - ay - t * dy))
    return best


@pytest.mark.parametrize("zoom", [11, 13, 15, 17])
def test_t_map_07_simplified_track_stays_within_the_zoom_tolerance(zoom: int) -> None:
    rng = np.random.default_rng(SEED)
    n = 3000
    steps = rng.normal(0, 3e-5, size=(n, 2)).cumsum(axis=0)
    lat, lon = 45.0 + steps[:, 0], 9.0 + steps[:, 1]
    track = ActivityTrack((TrackSegment(np.arange(n, dtype=float), lat, lon, None),))

    segments = simplify_for_zoom(track, zoom)
    tolerance = zoom_tolerance_m(zoom, float(lat.mean()))

    assert len(segments) == 1
    worst = max(
        distance_to_polyline_m(GeoPoint(float(a), float(b)), segments[0])
        for a, b in zip(lat, lon, strict=True)
    )
    assert worst <= tolerance * 1.05 + 0.5  # slack for the 1e-5 degree rounding


# --- resumable fill of the track boxes -------------------------------------------------------


@pytest.mark.parametrize("cut_after_batches", [0, 1, 2, 5])
def test_backfill_interrupted_and_resumed_ends_like_an_uninterrupted_one(
    cut_after_batches: int,
) -> None:
    def build() -> World:
        world = World()
        for i in range(23):
            world.add(summary_at(10.0 + i * 0.1, 20.0), days=i)
        world.profile.track_boxes._db.track_boxes.clear()  # type: ignore[attr-defined]
        return world

    reference, broken = build(), build()
    backfill_track_boxes(reference.profile.track_boxes, batch_size=4)

    calls = {"n": 0}

    def cancel() -> bool:
        calls["n"] += 1
        return calls["n"] > cut_after_batches

    first = backfill_track_boxes(broken.profile.track_boxes, batch_size=4, is_cancelled=cancel)
    assert first.cancelled
    assert not backfill_track_boxes(broken.profile.track_boxes, batch_size=4).cancelled

    ref_boxes = reference.profile.track_boxes._db.track_boxes  # type: ignore[attr-defined]
    got_boxes = broken.profile.track_boxes._db.track_boxes  # type: ignore[attr-defined]
    assert got_boxes == ref_boxes
    assert len(got_boxes) == 23
