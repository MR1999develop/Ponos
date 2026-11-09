# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: tile addresses and paths, cache limit."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.maps.domain import (
    DEFAULT_CACHE_BYTES,
    GIB,
    MAX_CACHE_BYTES,
    MAX_TILE_ZOOM,
    MIN_CACHE_BYTES,
    CacheLimit,
    TileAddress,
    TileAddressError,
    parse_cache_limit_gib,
    parse_tile_path,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/0/0/0", TileAddress(0, 0, 0)),
        ("0/0/0.png", TileAddress(0, 0, 0)),
        ("/12/2200/1500.png", TileAddress(12, 2200, 1500)),
        ("/19/524287/524287", TileAddress(19, 524287, 524287)),
        ("/03/001/002", TileAddress(3, 1, 2)),  # leading zeros are just digits
    ],
)
def test_t_sec_15_valid_tile_paths_become_addresses(path: str, expected: TileAddress) -> None:
    assert parse_tile_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/",
        "/1/2",  # missing a coordinate
        "/1/2/3/4",  # extra segment
        "/20/0/0",  # zoom above the limit
        "/-1/0/0",  # sign
        "/+1/0/0",
        "/1/2/3.jpg",  # other extension
        "/1/2/3.png.png",
        "/1/2/3?key=x",  # query smuggled in
        "/1/2/3#frag",
        "/ 1/2/3",
        "/1/2/3 ",
        "/1/\uff12/3",  # full-width digit
        "/1/2/3\n",
        "/../1/2/3",
        "/1/2/0",  # column out of range for zoom 1
        "/2/0/4",  # row out of range for zoom 2
        "/12/9999999/0",  # too many digits
        "/" + "9" * 40,  # too long: rejected before any parsing
    ],
)
def test_t_sec_15_malformed_or_out_of_range_tile_paths_are_rejected(path: str) -> None:
    with pytest.raises(TileAddressError):
        parse_tile_path(path)


@given(z=st.integers(0, MAX_TILE_ZOOM), data=st.data())
def test_every_tile_of_every_zoom_round_trips_through_its_path(z: int, data: st.DataObject) -> None:
    side = 1 << z
    x, y = data.draw(st.integers(0, side - 1)), data.draw(st.integers(0, side - 1))

    assert parse_tile_path(f"/{z}/{x}/{y}.png") == TileAddress(z, x, y)


@given(text=st.text(max_size=40))
def test_arbitrary_text_is_an_address_or_a_tile_address_error_never_anything_else(
    text: str,
) -> None:
    try:
        address = parse_tile_path(text)
    except TileAddressError:
        return
    assert 0 <= address.z <= MAX_TILE_ZOOM


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [(-1, 0, 0), (MAX_TILE_ZOOM + 1, 0, 0), (0, 1, 0), (0, 0, 1), (3, -1, 0), (3, 0, 8)],
)
def test_a_tile_address_outside_the_pyramid_is_rejected(z: int, x: int, y: int) -> None:
    with pytest.raises(TileAddressError):
        TileAddress(z, x, y)


@pytest.mark.parametrize("value", [True, 1.0, "1", None])
def test_a_tile_address_made_of_non_integers_is_rejected(value: Any) -> None:
    with pytest.raises(TileAddressError):
        TileAddress(value, 0, 0)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4", 4 * GIB),
        (" 0,5 ", GIB // 2),
        ("0.5", GIB // 2),
        ("2,25", 2 * GIB + GIB // 4),
        ("64", 64 * GIB),
    ],
)
def test_the_cache_size_accepts_comma_and_point(text: str, expected: int) -> None:
    assert parse_cache_limit_gib(text).bytes == expected


@pytest.mark.parametrize(
    "text", ["", "abc", "-1", "0", "0,01", "65", "1e3", "4 GB", "1,2,3", "9" * 20]
)
def test_a_cache_size_out_of_range_or_not_a_number_is_rejected(text: str) -> None:
    with pytest.raises(ValueError, match=r"not a size|out of range"):
        parse_cache_limit_gib(text)


def test_the_cache_limit_has_a_floor_and_a_ceiling() -> None:
    assert CacheLimit(MIN_CACHE_BYTES).bytes == MIN_CACHE_BYTES
    assert CacheLimit(MAX_CACHE_BYTES).bytes == MAX_CACHE_BYTES
    for bad in (MIN_CACHE_BYTES - 1, MAX_CACHE_BYTES + 1, 0, -5, True):
        with pytest.raises(ValueError, match="out of range"):
            CacheLimit(bad)


def test_the_default_cache_limit_is_four_gibibytes() -> None:
    assert CacheLimit(DEFAULT_CACHE_BYTES).bytes == 4 * GIB
