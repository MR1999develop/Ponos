# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Full track, box with antimeridian, simplification by zoom and reading from files."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.decoding import (
    ActivityFileError,
    decode_stored_activity,
    decode_stored_track,
)
from ponos_coach.activities.domain import (
    MAX_TRACK_POINTS,
    ActivityTrack,
    FileFormat,
    GeoBox,
    GeoPoint,
    TrackSegment,
    build_track,
    decode_segments,
    encode_segments,
    simplify_for_zoom,
    zoom_tolerance_m,
)
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
SEA = (-30.0, -20.0)  # invented coordinates, in open sea
DEG_PER_M = 1.0 / 111_195.08


def segment(
    count: int = 5, *, lat0: float = 10.0, lon0: float = 20.0, step_s: float = 1.0
) -> TrackSegment:
    return TrackSegment(
        np.arange(count, dtype=np.float64) * step_s,
        lat0 + np.arange(count, dtype=np.float64) * 0.001,
        np.full(count, lon0),
        np.full(count, 100.0),
    )


# --- TrackSegment and ActivityTrack ------------------------------------------------------------


def test_segment_arrays_are_copied_and_read_only() -> None:
    lat = np.array([1.0, 2.0])
    track = TrackSegment(np.array([0.0, 1.0]), lat, np.array([3.0, 4.0]))
    lat[0] = 50.0

    assert track.lat_deg[0] == 1.0
    assert not track.lat_deg.flags.writeable
    assert track.altitude_m is None


@pytest.mark.parametrize(
    ("seconds", "lat", "lon", "altitude"),
    [
        ([], [], [], None),  # empty
        ([0.0, 1.0], [1.0], [1.0, 2.0], None),  # different lengths
        ([0.0, 1.0], [1.0, 2.0], [1.0, 2.0], [1.0]),  # altitude of a different length
        ([0.0, 0.0], [1.0, 2.0], [1.0, 2.0], None),  # non-increasing seconds
        ([1.0, 0.0], [1.0, 2.0], [1.0, 2.0], None),
        ([0.0, math.nan], [1.0, 2.0], [1.0, 2.0], None),
        ([0.0, 1.0], [math.inf, 2.0], [1.0, 2.0], None),
        ([0.0, 1.0], [91.0, 2.0], [1.0, 2.0], None),  # latitude out of range
        ([0.0, 1.0], [1.0, 2.0], [1.0, 181.0], None),  # longitude out of range
        ([0.0, 1.0], [1.0, 2.0], [1.0, 2.0], [1.0, math.inf]),  # infinite altitude
    ],
)
def test_invalid_segments_are_rejected(
    seconds: list[float], lat: list[float], lon: list[float], altitude: list[float] | None
) -> None:
    with pytest.raises(ValueError, match=r"."):
        TrackSegment(
            np.array(seconds),
            np.array(lat),
            np.array(lon),
            None if altitude is None else np.array(altitude),
        )


def test_unknown_altitude_is_nan_and_allowed() -> None:
    track = TrackSegment(
        np.array([0.0, 1.0]), np.array([1.0, 2.0]), np.array([1.0, 2.0]), np.array([5.0, math.nan])
    )

    assert math.isnan(track.altitude_m[1])  # type: ignore[index]


def test_track_needs_a_segment_and_segments_in_time_order() -> None:
    with pytest.raises(ValueError, match="at least one segment"):
        ActivityTrack(())
    with pytest.raises(ValueError, match="overlap"):
        ActivityTrack((segment(3), segment(3)))


# --- build_track: gaps and time ---------------------------------------------------------------


def test_a_gap_over_the_limit_splits_the_track() -> None:
    seconds = np.array([0.0, 1.0, 2.0, 40.0, 41.0])  # 38 s gap
    lat = np.linspace(1.0, 2.0, 5)
    track = build_track(seconds, lat, lat, None, np.zeros(5, dtype=np.intp))

    assert track is not None
    assert [len(s) for s in track.segments] == [3, 2]


def test_a_gap_of_exactly_the_limit_does_not_split() -> None:
    seconds = np.array([0.0, 30.0, 60.0])
    lat = np.array([1.0, 2.0, 3.0])
    track = build_track(seconds, lat, lat, None, np.zeros(3, dtype=np.intp))

    assert track is not None
    assert len(track.segments) == 1


def test_a_change_of_run_splits_the_track() -> None:
    seconds = np.arange(4.0)
    lat = np.array([1.0, 2.0, 3.0, 4.0])
    track = build_track(seconds, lat, lat, None, np.array([0, 0, 1, 1], dtype=np.intp))

    assert track is not None
    assert [len(s) for s in track.segments] == [2, 2]


def test_repeated_and_backwards_times_are_dropped_keeping_the_first() -> None:
    seconds = np.array([0.0, 1.0, 1.0, 0.5, 2.0])
    lat = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    track = build_track(seconds, lat, lat, None, np.zeros(5, dtype=np.intp))

    assert track is not None
    assert track.segments[0].lat_deg.tolist() == [1.0, 2.0, 5.0]


def test_no_points_is_no_track() -> None:
    empty = np.array([], dtype=np.float64)

    assert build_track(empty, empty, empty, None, np.array([], dtype=np.intp)) is None


@given(
    st.lists(st.floats(0.1, 100.0), min_size=1, max_size=60),
    st.lists(st.booleans(), min_size=60, max_size=60),
)
def test_segments_partition_the_points_in_order(steps: list[float], cuts: list[bool]) -> None:
    seconds = np.cumsum(steps)
    lat = np.linspace(-10.0, 10.0, len(steps))
    run = np.cumsum(cuts[: len(steps)]).astype(np.intp)

    track = build_track(seconds, lat, lat, None, run)

    assert track is not None
    assert np.concatenate([s.lat_deg for s in track.segments]).tolist() == lat.tolist()
    assert track.point_count == len(steps)


# --- Box and antimeridian -----------------------------------------------------------------


def test_bounding_box_of_a_track_across_the_antimeridian_is_narrow() -> None:
    lon = np.array([179.9, 179.99, -179.99, -179.9])
    track = ActivityTrack((TrackSegment(np.arange(4.0), np.array([0.0, 0.1, 0.2, 0.3]), lon),))

    box = track.bounding_box()

    assert box.crosses_antimeridian
    assert (box.west, box.east) == (179.9, -179.9)
    assert (box.south, box.north) == (0.0, 0.3)
    assert box.contains(0.15, 180.0)
    assert box.contains(0.15, -180.0)
    assert not box.contains(0.15, 0.0)


@given(
    st.lists(st.tuples(st.floats(-90, 90), st.floats(-180, 180)), min_size=1, max_size=40),
)
def test_box_of_points_contains_them_and_is_not_wider_than_the_naive_one(
    pairs: list[tuple[float, float]],
) -> None:
    lat = np.array([p[0] for p in pairs])
    lon = np.array([p[1] for p in pairs])

    box = GeoBox.of_points(lat, lon)

    assert box.contains_many(lat, lon).all()
    width = box.east - box.west + (360.0 if box.crosses_antimeridian else 0.0)
    assert width <= lon.max() - lon.min() + 1e-9


def test_view_wider_than_the_globe_is_the_whole_globe() -> None:
    box = GeoBox.from_view(-95.0, -400.0, 95.0, 400.0)

    assert (box.south, box.west, box.north, box.east) == (-90.0, -180.0, 90.0, 180.0)


def test_view_beyond_the_antimeridian_is_wrapped() -> None:
    box = GeoBox.from_view(0.0, 170.0, 10.0, 190.0)  # "copy" of the world to the east

    assert (box.west, box.east) == (170.0, -170.0)
    assert box.contains(5.0, -175.0)
    assert box.center() == (5.0, 180.0)


def test_invalid_views_and_boxes_are_rejected() -> None:
    with pytest.raises(ValueError, match="precedes"):
        GeoBox.from_view(0.0, 10.0, 1.0, 5.0)
    with pytest.raises(ValueError, match="finite"):
        GeoBox.from_view(math.nan, 0.0, 1.0, 5.0)
    with pytest.raises(ValueError, match="latitudes"):
        GeoBox(10.0, 0.0, 5.0, 1.0)


# --- Tolerance by zoom and simplification ----------------------------------------------------


def test_zoom_tolerance_at_the_equator_by_hand() -> None:
    # 2 pi R / 256 = 156368.08 m per pixel at zoom 0; it halves at every level.
    assert zoom_tolerance_m(0, 0.0) == pytest.approx(156_368.08, rel=1e-6)
    assert zoom_tolerance_m(10, 0.0) == pytest.approx(152.7032, rel=1e-5)
    assert zoom_tolerance_m(10, 60.0) == pytest.approx(152.7032 / 2.0, rel=1e-5)


def test_zoom_is_clamped_and_poles_do_not_reach_zero() -> None:
    assert zoom_tolerance_m(99, 0.0) == zoom_tolerance_m(22, 0.0)
    assert zoom_tolerance_m(-5, 0.0) == zoom_tolerance_m(0, 0.0)
    assert zoom_tolerance_m(5, 90.0) > 0.0
    with pytest.raises(ValueError, match="finite"):
        zoom_tolerance_m(5, math.nan)


def wiggly_track(count: int = 600) -> ActivityTrack:
    """Path with deviations of 1 to 40 m over 3 km: the shape depends on the zoom level."""
    i = np.arange(count, dtype=np.float64)
    lon = 20.0 + i * (5.0 * DEG_PER_M)
    lat = 10.0 + 40.0 * DEG_PER_M * np.sin(i / 7.0) * np.exp(-((i % 100) / 80.0))
    return ActivityTrack((TrackSegment(i, lat, lon),))


def test_lower_zoom_keeps_fewer_points_and_the_ends() -> None:
    track = wiggly_track()
    counts = [sum(len(s) for s in simplify_for_zoom(track, z)) for z in (8, 12, 16, 20)]

    assert counts == sorted(counts)
    assert counts[0] < counts[-1]
    for zoom in (8, 20):
        (only,) = simplify_for_zoom(track, zoom)
        assert only[0] == GeoPoint(float(track.segments[0].lat_deg[0]), 20.0)
        assert only[-1].lon_deg == pytest.approx(float(track.segments[0].lon_deg[-1]))


def test_single_point_segments_are_omitted() -> None:
    track = ActivityTrack((segment(1), replace_start(segment(4), 100.0)))

    result = simplify_for_zoom(track, 16)

    assert len(result) == 1


def replace_start(seg: TrackSegment, offset_s: float) -> TrackSegment:
    return TrackSegment(seg.seconds + offset_s, seg.lat_deg, seg.lon_deg, seg.altitude_m)


def test_track_across_the_antimeridian_is_simplified_without_a_jump() -> None:
    lon = np.linspace(179.99, -179.99, 50) % 360.0  # crosses from east to west
    lon = np.where(lon > 180.0, lon - 360.0, lon)
    track = ActivityTrack((TrackSegment(np.arange(50.0), np.full(50, 5.0), lon),))

    (only,) = simplify_for_zoom(track, 10)

    assert len(only) == 2  # a straight line reduces to its ends, without going through Greenwich


def test_a_budget_overflow_still_gives_an_encodable_track() -> None:
    rng = np.random.default_rng(7)
    segments = []
    for k in range(30):  # 30 very noisy segments of 400 points
        lat = 10.0 + k * 0.01 + rng.normal(0, 3e-4, 400)
        lon = 20.0 + rng.normal(0, 3e-4, 400)
        segments.append(
            TrackSegment(np.arange(400.0) + k * 1000.0, lat, lon),
        )
    result = simplify_for_zoom(ActivityTrack(tuple(segments)), 22)

    assert 0 < sum(len(s) for s in result) <= MAX_TRACK_POINTS
    assert decode_segments(encode_segments(result))  # does not raise


# --- Compact encoding ------------------------------------------------------------------------


def test_segments_round_trip_within_the_polyline_precision() -> None:
    original = (
        (GeoPoint(10.00001, 20.00002), GeoPoint(10.5, 20.5)),
        (GeoPoint(-33.3, 179.99999), GeoPoint(-33.31, -179.99999)),
    )

    decoded = decode_segments(encode_segments(original))

    assert len(decoded) == 2
    for before, after in zip(original, decoded, strict=True):
        for a, b in zip(before, after, strict=True):
            assert (a.lat_deg, a.lon_deg) == pytest.approx((b.lat_deg, b.lon_deg), abs=0.6e-5)


@given(
    st.lists(
        st.lists(
            st.builds(GeoPoint, st.floats(-90, 90), st.floats(-180, 180)), min_size=1, max_size=20
        ),
        max_size=6,
    )
)
def test_encoding_round_trip_property(segments: list[list[GeoPoint]]) -> None:
    decoded = decode_segments(encode_segments(segments))

    assert [len(s) for s in decoded] == [len(s) for s in segments]
    for before, after in zip(segments, decoded, strict=True):
        for a, b in zip(before, after, strict=True):
            assert abs(a.lat_deg - b.lat_deg) <= 0.6e-5
            assert abs(a.lon_deg - b.lon_deg) <= 0.6e-5


def test_no_segments_is_the_empty_string() -> None:
    assert encode_segments(()) == ""
    assert decode_segments("") == ()


@pytest.mark.parametrize("hostile", ["a b", "?? ", " ??", "\x00", "x" * 40_000, "_p~iF"])
def test_hostile_encoded_tracks_are_rejected(hostile: str) -> None:
    with pytest.raises(ValueError, match=r"."):
        decode_segments(hostile)


def test_encoding_refuses_too_many_points_and_empty_segments() -> None:
    point = GeoPoint(1.0, 1.0)
    with pytest.raises(ValueError, match="too many"):
        encode_segments([[point] * (MAX_TRACK_POINTS + 1)])
    with pytest.raises(ValueError, match="empty"):
        encode_segments([[]])


# --- Reading from files -------------------------------------------------------------------------


def fit_with_gps(positions: list[tuple[float, float] | None]) -> bytes:
    start = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    samples = []
    for i, position in enumerate(positions):
        lat, lon = (None, None) if position is None else position
        samples.append(
            fw.Sample(
                fw.datetime_to_fit(start) + i, 120, lat_deg=lat, lon_deg=lon, altitude_m=100.0 + i
            )
        )
    spec = fw.ActivitySpec(start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples)
    return fw.build_activity(spec)


def test_fit_track_has_the_hr_time_axis_altitude_and_splits_at_missing_gps() -> None:
    step = 3.0 * DEG_PER_M
    positions: list[tuple[float, float] | None] = [
        (SEA[0] + i * step, SEA[1]) if i not in (3, 4) else None for i in range(8)
    ]

    track = decode_stored_track(fit_with_gps(positions), FileFormat.FIT)

    assert track is not None
    assert [s.seconds.tolist() for s in track.segments] == [[0.0, 1.0, 2.0], [5.0, 6.0, 7.0]]
    assert track.segments[1].altitude_m is not None
    assert track.segments[1].altitude_m.tolist() == pytest.approx([105.0, 106.0, 107.0], abs=0.2)
    assert track.segments[0].lat_deg[0] == pytest.approx(SEA[0], abs=1e-6)


def test_fit_without_positions_has_no_track() -> None:
    assert decode_stored_track(fit_with_gps([None] * 4), FileFormat.FIT) is None


def test_gpx_track_splits_by_segment_and_by_pause() -> None:
    first = gw.line_points(START, 3, step_s=10, lat_step_deg=0.001, ele_m=[1.0, 2.0, None])
    second = gw.line_points(
        START + timedelta(seconds=100), 2, step_s=10, lat_step_deg=0.001, first_lat=SEA[0]
    )
    pause = gw.line_points(START + timedelta(seconds=300), 2, step_s=10, first_lat=SEA[0])
    data = gw.build_gpx(gw.GpxSpec([first, second + pause]))

    track = decode_stored_track(data, FileFormat.GPX)

    assert track is not None
    assert [len(s) for s in track.segments] == [3, 2, 2]  # trkseg, then the 190 s pause
    assert track.segments[0].altitude_m is not None
    assert track.segments[0].altitude_m[:2].tolist() == [1.0, 2.0]
    assert math.isnan(track.segments[0].altitude_m[2])
    assert track.segments[1].seconds[0] == 100.0


def test_corrupt_and_empty_files_are_activity_file_errors() -> None:
    with pytest.raises(ActivityFileError):
        decode_stored_track(b"", FileFormat.FIT)
    with pytest.raises(ActivityFileError):
        decode_stored_track(b"not a fit file at all", FileFormat.FIT)
    with pytest.raises(ActivityFileError):
        decode_stored_track(b"<gpx", FileFormat.GPX)


def test_reading_the_track_does_not_change_the_activity() -> None:
    data = fit_with_gps([(SEA[0] + i * 3.0 * DEG_PER_M, SEA[1]) for i in range(8)])
    before = decode_stored_activity(data, FileFormat.FIT)

    decode_stored_track(data, FileFormat.FIT)

    after = decode_stored_activity(data, FileFormat.FIT)
    assert after.summary == before.summary
    assert after.heart_rate is not None and before.heart_rate is not None
    assert after.heart_rate.bpm.tolist() == before.heart_rate.bpm.tolist()
    assert (after.elapsed_s, after.timer_s) == (before.elapsed_s, before.timer_s)
