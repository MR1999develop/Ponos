# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: tile addresses and paths, cache limit."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.maps.domain import (
    BACKGROUNDS,
    CYCLOSM,
    DEFAULT_BACKGROUND,
    DEFAULT_CACHE_BYTES,
    GIB,
    MAX_CACHE_BYTES,
    MAX_TILE_ZOOM,
    MIN_CACHE_BYTES,
    OPENSTREETMAP,
    OPENTOPOMAP,
    CacheLimit,
    MapBackground,
    TileAddress,
    TileAddressError,
    background_by_key,
    parse_cache_limit_gib,
    parse_tile_path,
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/openstreetmap/0/0/0", TileAddress(0, 0, 0, OPENSTREETMAP)),
        ("openstreetmap/0/0/0.png", TileAddress(0, 0, 0, OPENSTREETMAP)),
        ("/openstreetmap/12/2200/1500.png", TileAddress(12, 2200, 1500, OPENSTREETMAP)),
        ("/openstreetmap/19/524287/524287", TileAddress(19, 524287, 524287, OPENSTREETMAP)),
        ("/opentopomap/17/1/2", TileAddress(17, 1, 2, OPENTOPOMAP)),
        ("/cyclosm/20/1048575/1048575.png", TileAddress(20, 1048575, 1048575, CYCLOSM)),
        ("/cyclosm/03/001/002", TileAddress(3, 1, 2, CYCLOSM)),  # leading zeros are just digits
    ],
)
def test_t_sec_15_valid_tile_paths_become_addresses(path: str, expected: TileAddress) -> None:
    assert parse_tile_path(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/1/2/3",  # the old form, without a source
        "/openstreetmap/20/0/0",  # zoom above the maximum of OpenStreetMap
        "/google/1/0/0",  # a source that is not in the set
        "/openstreetmap/-1/0/0",  # sign
        "/openstreetmap/1/2/3?key=x",  # query smuggled in
        "/openstreetmap/1/\uff12/3",  # full-width digit
        "/openstreetmap/1/2/3\n",
        "/../openstreetmap/1/2/3",
        "/openstreetmap/1/2/0",  # column out of range for zoom 1
        "/openstreetmap/" + "9" * 60,  # too long: rejected before any parsing
    ],
)
def test_t_sec_15_malformed_or_out_of_range_tile_paths_are_rejected(path: str) -> None:
    with pytest.raises(TileAddressError):
        parse_tile_path(path)


@given(background=st.sampled_from(BACKGROUNDS), data=st.data())
def test_every_tile_of_every_background_round_trips_through_its_path(
    background: MapBackground, data: st.DataObject
) -> None:
    z = data.draw(st.integers(0, background.max_zoom))
    side = 1 << z
    x, y = data.draw(st.integers(0, side - 1)), data.draw(st.integers(0, side - 1))

    path = f"/{background.key}/{z}/{x}/{y}.png"

    assert parse_tile_path(path) == TileAddress(z, x, y, background)


@given(text=st.text(max_size=40))
def test_arbitrary_text_is_an_address_or_a_tile_address_error_never_anything_else(
    text: str,
) -> None:
    try:
        address = parse_tile_path(text)
    except TileAddressError:
        return
    assert 0 <= address.z <= address.background.max_zoom <= MAX_TILE_ZOOM


@pytest.mark.parametrize(
    ("z", "x", "y"),
    [(-1, 0, 0), (MAX_TILE_ZOOM + 1, 0, 0), (0, 1, 0), (0, 0, 1), (3, -1, 0), (3, 0, 8)],
)
def test_a_tile_address_outside_the_pyramid_is_rejected(z: int, x: int, y: int) -> None:
    with pytest.raises(TileAddressError):
        TileAddress(z, x, y)


@pytest.mark.parametrize(
    ("background", "highest"), [(OPENSTREETMAP, 19), (OPENTOPOMAP, 17), (CYCLOSM, 20)]
)
def test_each_background_has_its_own_highest_zoom(background: MapBackground, highest: int) -> None:
    assert background.max_zoom == highest
    assert TileAddress(highest, 0, 0, background).z == highest
    with pytest.raises(TileAddressError):
        TileAddress(highest + 1, 0, 0, background)


def test_the_closed_set_has_the_three_backgrounds_with_stable_keys_and_credits() -> None:
    assert [b.key for b in BACKGROUNDS] == ["openstreetmap", "opentopomap", "cyclosm"]
    assert DEFAULT_BACKGROUND is OPENSTREETMAP
    assert OPENSTREETMAP.attribution == "\u00a9 OpenStreetMap contributors"
    assert OPENTOPOMAP.attribution == (
        "Map data: \u00a9 OpenStreetMap contributors, SRTM | "
        "Map style: \u00a9 OpenTopoMap (CC-BY-SA)"
    )
    assert CYCLOSM.attribution == "CyclOSM | Map data: \u00a9 OpenStreetMap contributors"
    assert len({b.key for b in BACKGROUNDS}) == 3


def test_a_background_is_found_by_its_key_and_nothing_else_is_accepted() -> None:
    assert background_by_key("cyclosm") is CYCLOSM
    for bad in ("", "CyclOSM", "https://example.org/{z}/{x}/{y}.png", None, 3, "cyclosm "):
        with pytest.raises(TileAddressError):
            background_by_key(bad)


@pytest.mark.parametrize(
    "fields",
    [
        {"key": "Bad1"},
        {"host": "evil.example.org/path"},
        {"host": "{s}.example.org", "subdomains": ()},  # placeholder without subdomains
        {"subdomains": ("a",)},  # subdomains without placeholder
        {"path_prefix": "cyclosm"},
        {"max_zoom": 21},
        {"attribution": ""},
    ],
)
def test_a_background_that_is_not_well_formed_cannot_be_built(fields: dict[str, Any]) -> None:
    base: dict[str, Any] = {
        "key": "x",
        "label": "X",
        "host": "tile.example.org",
        "subdomains": (),
        "path_prefix": "",
        "max_zoom": 10,
        "attribution": "credit",
    }
    with pytest.raises(ValueError, match="invalid map background"):
        MapBackground(**(base | fields))


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
