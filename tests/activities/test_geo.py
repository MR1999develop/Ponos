# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""GeoPoint, track simplification (RDP) and encoded polylines."""

from __future__ import annotations

import contextlib
import inspect
import math
import random

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities import fit_decoder
from ponos_coach.activities.domain import (
    MAX_POLYLINE_CHARS,
    MAX_TRACK_POINTS,
    GeoPoint,
    decode_polyline,
    encode_polyline,
    geo,
    simplify_indices,
    simplify_track,
)

EARTH_RADIUS_M = 6_371_008.8
METERS_PER_DEGREE = math.radians(1.0) * EARTH_RADIUS_M

latitudes = st.floats(-90.0, 90.0, allow_nan=False)
longitudes = st.floats(-180.0, 180.0, allow_nan=False)
points = st.builds(GeoPoint, latitudes, longitudes)


# --- GeoPoint -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lat", "lon"), [(0.0, 0.0), (90.0, 180.0), (-90.0, -180.0), (-30.5, -20.25)]
)
def test_valid_points_are_accepted(lat: float, lon: float) -> None:
    assert GeoPoint(lat, lon).lat_deg == lat


@pytest.mark.parametrize(
    ("lat", "lon"),
    [
        (90.000001, 0.0),
        (-90.000001, 0.0),
        (0.0, 180.000001),
        (0.0, -180.000001),
        (math.nan, 0.0),
        (0.0, math.inf),
    ],
)
def test_points_outside_the_globe_are_refused(lat: float, lon: float) -> None:
    with pytest.raises(ValueError, match=r"latitude|longitude|finite"):
        GeoPoint(lat, lon)


# --- Simplification -------------------------------------------------------------------------


def naive_rdp(x: list[float], y: list[float], first: int, last: int, tol: float) -> set[int]:
    """Recursive, naive Ramer-Douglas-Peucker, as a reference."""
    if last - first < 2:
        return {first, last}
    sx, sy = x[last] - x[first], y[last] - y[first]
    length_sq = sx * sx + sy * sy
    best, best_i = -1.0, first
    for i in range(first + 1, last):
        px, py = x[i] - x[first], y[i] - y[first]
        if length_sq == 0.0:
            d = math.hypot(px, py)
        else:
            t = max(0.0, min(1.0, (px * sx + py * sy) / length_sq))
            d = math.hypot(px - t * sx, py - t * sy)
        if d > best:
            best, best_i = d, i
    if best <= tol:
        return {first, last}
    return naive_rdp(x, y, first, best_i, tol) | naive_rdp(x, y, best_i, last, tol)


def naive_projection(lat: list[float], lon: list[float]) -> tuple[list[float], list[float]]:
    lat_r = [math.radians(v) for v in lat]
    lon_r = np.unwrap([math.radians(v) for v in lon]).tolist()
    scale = math.cos(sum(lat_r) / len(lat_r))
    x = [(v - lon_r[0]) * scale * EARTH_RADIUS_M for v in lon_r]
    y = [(v - lat_r[0]) * EARTH_RADIUS_M for v in lat_r]
    return x, y


def test_a_straight_line_collapses_to_its_ends() -> None:
    line = [GeoPoint(10.0, 20.0 + i * 0.0001) for i in range(50)]

    assert simplify_track(line) == (line[0], line[-1])


def test_a_corner_beyond_the_tolerance_is_kept() -> None:
    corner = GeoPoint(10.0, 20.001)  # 110 m east of the start
    track = (GeoPoint(10.0, 20.0), corner, GeoPoint(10.001, 20.001))

    assert simplify_track(track) == track


def test_tolerance_boundary_on_and_off_point() -> None:
    # Middle point exactly 10 m from the segment (north of the east-west line).
    offset_deg = 10.0 / METERS_PER_DEGREE
    start, end = GeoPoint(0.0, 0.0), GeoPoint(0.0, 0.002)
    middle = GeoPoint(offset_deg, 0.001)

    assert len(simplify_track((start, middle, end), tolerance_m=10.0 + 1e-6)) == 2
    assert len(simplify_track((start, middle, end), tolerance_m=10.0 - 1e-6)) == 3


def test_zero_tolerance_keeps_every_non_collinear_point() -> None:
    zigzag = tuple(GeoPoint(0.0001 * (i % 2), 0.0001 * i) for i in range(10))

    assert simplify_track(zigzag, tolerance_m=0.0) == zigzag


def test_short_tracks_come_back_unchanged() -> None:
    assert simplify_track(()) == ()
    one = (GeoPoint(1.0, 2.0),)
    two = (GeoPoint(1.0, 2.0), GeoPoint(3.0, 4.0))
    assert simplify_track(one) == one
    assert simplify_track(two) == two


def test_the_cap_limits_the_points_and_keeps_the_ends() -> None:
    rng = random.Random(7)
    walk = [GeoPoint(-30.0 + rng.uniform(-0.01, 0.01), -20.0 + i * 0.0001) for i in range(500)]

    kept = simplify_track(walk, tolerance_m=0.0, max_points=20)

    assert len(kept) == 20
    assert kept[0] == walk[0]
    assert kept[-1] == walk[-1]


def test_cap_keeps_the_most_significant_points() -> None:
    # A 500 m spike amid noise of a few metres: with cap 3 the spike stays.
    rng = random.Random(3)
    track = [GeoPoint(rng.uniform(0, 0.00002), i * 0.0001) for i in range(101)]
    peak = GeoPoint(0.0045, 50 * 0.0001)
    track[50] = peak

    assert peak in simplify_track(track, tolerance_m=0.0, max_points=3)


def test_the_default_cap_is_the_project_cap() -> None:
    rng = random.Random(11)
    n = 20_000
    lat = np.cumsum([rng.uniform(-1, 1) * 1e-4 for _ in range(n)]) + -30.0
    lon = np.cumsum([rng.uniform(-1, 1) * 1e-4 for _ in range(n)]) + -20.0

    indices = simplify_indices(lat, lon, tolerance_m=0.0)

    assert len(indices) == MAX_TRACK_POINTS
    assert indices[0] == 0
    assert indices[-1] == n - 1
    assert (np.diff(indices) > 0).all()


def test_a_track_crossing_the_antimeridian_is_still_a_straight_line() -> None:
    lon = [179.998, 179.999, 180.0, -179.999, -179.998]
    track = tuple(GeoPoint(0.0, v) for v in lon)

    assert simplify_track(track) == (track[0], track[-1])


def test_simplification_is_deterministic() -> None:
    rng = random.Random(5)
    track = [GeoPoint(rng.uniform(-30.1, -29.9), rng.uniform(-20.1, -19.9)) for _ in range(300)]

    assert simplify_track(track, 50.0, 40) == simplify_track(track, 50.0, 40)


@pytest.mark.parametrize(
    ("tolerance", "max_points"), [(-1.0, 10), (math.nan, 10), (math.inf, 10), (5.0, 1), (5.0, 0)]
)
def test_invalid_parameters_are_refused(tolerance: float, max_points: int) -> None:
    with pytest.raises(ValueError, match="tolerance"):
        simplify_indices([0.0, 1.0, 2.0], [0.0, 1.0, 2.0], tolerance, max_points)


def test_mismatched_or_non_finite_columns_are_refused() -> None:
    with pytest.raises(ValueError, match="same length"):
        simplify_indices([0.0, 1.0], [0.0])
    with pytest.raises(ValueError, match="finite"):
        simplify_indices([0.0, math.nan, 2.0], [0.0, 1.0, 2.0])


@given(
    st.lists(st.tuples(st.floats(-30.1, -29.9), st.floats(-20.1, -19.9)), min_size=0, max_size=60),
    st.floats(0.5, 500.0),
)
def test_iterative_version_matches_the_naive_recursive_one(
    coordinates: list[tuple[float, float]], tolerance: float
) -> None:
    lat = [c[0] for c in coordinates]
    lon = [c[1] for c in coordinates]

    got = set(simplify_indices(lat, lon, tolerance, max_points=10_000).tolist())

    if len(coordinates) <= 2:
        assert got == set(range(len(coordinates)))
    else:
        x, y = naive_projection(lat, lon)
        assert got == naive_rdp(x, y, 0, len(coordinates) - 1, tolerance)


@given(
    st.lists(st.tuples(st.floats(-30.1, -29.9), st.floats(-20.1, -19.9)), min_size=3, max_size=80),
    st.floats(0.0, 300.0),
    st.integers(2, 30),
)
def test_result_is_a_sorted_subset_with_ends_within_the_cap(
    coordinates: list[tuple[float, float]], tolerance: float, cap: int
) -> None:
    lat = [c[0] for c in coordinates]
    lon = [c[1] for c in coordinates]

    indices = simplify_indices(lat, lon, tolerance, cap)

    assert indices[0] == 0
    assert indices[-1] == len(coordinates) - 1
    assert len(indices) <= max(cap, 2)
    assert (np.diff(indices) > 0).all()


# --- Polyline -------------------------------------------------------------------------------


def test_encoding_matches_the_published_reference_example() -> None:
    reference = (GeoPoint(38.5, -120.2), GeoPoint(40.7, -120.95), GeoPoint(43.252, -126.453))

    assert encode_polyline(reference) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
    assert decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@") == reference


def test_empty_track_is_the_empty_string() -> None:
    assert encode_polyline(()) == ""
    assert decode_polyline("") == ()


@given(st.lists(points, max_size=60))
def test_decoding_returns_the_points_within_the_precision(track: list[GeoPoint]) -> None:
    decoded = decode_polyline(encode_polyline(track))

    assert len(decoded) == len(track)
    for original, back in zip(track, decoded, strict=True):
        assert abs(original.lat_deg - back.lat_deg) <= 0.5e-5 + 1e-9
        assert abs(original.lon_deg - back.lon_deg) <= 0.5e-5 + 1e-9


@given(st.lists(points, max_size=60))
def test_a_decoded_polyline_encodes_to_itself(track: list[GeoPoint]) -> None:
    encoded = encode_polyline(track)

    assert encode_polyline(decode_polyline(encoded)) == encoded


def test_extreme_corners_round_trip() -> None:
    corners = (GeoPoint(-90.0, -180.0), GeoPoint(90.0, 180.0), GeoPoint(-90.0, 180.0))

    assert decode_polyline(encode_polyline(corners)) == corners


def test_too_many_points_are_refused_when_encoding() -> None:
    with pytest.raises(ValueError, match="too many"):
        encode_polyline([GeoPoint(0.0, 0.0)] * (MAX_TRACK_POINTS + 1))


def encode_raw(values: list[int]) -> str:
    """Encoding of already scaled integers (reference to build hostile polylines)."""
    out = []
    for value in values:
        rest = ~(value << 1) if value < 0 else value << 1
        while rest >= 0x20:
            out.append(chr((0x20 | (rest & 0x1F)) + 63))
            rest >>= 5
        out.append(chr(rest + 63))
    return "".join(out)


@pytest.mark.parametrize(
    ("hostile", "message"),
    [
        ("_p~i F", "character"),  # space
        ("_p~iF\x7f", "character"),  # DEL
        ("_p~iF\n", "character"),
        ("_p~iFè", "character"),
        ("_", "incomplete"),  # value with continuation and then the end
        ("_p~iF", "incomplete"),  # a single coordinate
        ("a" * (MAX_POLYLINE_CHARS + 1), "too long"),
        ("_" * 8, "too large"),
        (encode_raw([9_100_000, 0]), "latitude"),
        (encode_raw([0, 18_100_000]), "longitude"),
    ],
)
def test_hostile_polylines_are_refused(hostile: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_polyline(hostile)


@given(st.text(max_size=200))
def test_arbitrary_text_never_crashes_the_decoder(text: str) -> None:
    with contextlib.suppress(ValueError):
        decode_polyline(text)


def test_earth_radius_is_defined_once_in_geo() -> None:
    assert geo.EARTH_RADIUS_M == 6_371_008.8
    assert "6_371_008" not in inspect.getsource(fit_decoder)
