# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: tile addresses and paths, styles, key, cache limit."""

from __future__ import annotations

import copy
import pickle
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
    NEUTRAL_STYLE,
    CacheLimit,
    MapSettings,
    TileAddress,
    TileAddressError,
    TileKey,
    parse_cache_limit_gib,
    parse_tile_path,
    tile_style,
)

KEY_TEXT = "chiave-di-prova-0123456789"


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
    ("language", "style"),
    [
        ("it", "it"),
        ("en", "en"),
        ("it_IT", "it"),
        ("EN-gb", "en"),
        (" it ", "it"),
        ("de", NEUTRAL_STYLE),
        ("", NEUTRAL_STYLE),
        ("../../x", NEUTRAL_STYLE),
        ("italiano", NEUTRAL_STYLE),
    ],
)
def test_the_style_follows_the_language_and_falls_back_to_the_neutral_one(
    language: str, style: str
) -> None:
    assert tile_style(language) == style


def test_the_key_is_a_secret_that_never_shows_itself() -> None:
    key = TileKey(KEY_TEXT)

    assert KEY_TEXT not in repr(key)
    assert KEY_TEXT not in str(key)
    assert KEY_TEXT not in f"{key}"
    assert KEY_TEXT not in f"{[key]}"
    assert key.reveal() == KEY_TEXT
    with pytest.raises(TypeError):
        hash(key)
    with pytest.raises(TypeError):
        pickle.dumps(key)
    with pytest.raises(TypeError):
        copy.deepcopy(key)
    assert key == TileKey(KEY_TEXT)
    assert key != TileKey(KEY_TEXT + "x")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "short",
        "con spazio dentro",
        "chiave?key=altro",
        "a&b=cdefghij",
        "x" * 129,
        "chiave\n12345",
        "chiàve12345",
    ],
)
def test_a_key_that_is_not_url_safe_is_rejected_without_repeating_it(text: str) -> None:
    with pytest.raises(ValueError, match="invalid tile key") as raised:
        TileKey(text)

    assert text not in str(raised.value) or text == ""


def test_the_key_the_user_pastes_loses_the_surrounding_whitespace() -> None:
    assert TileKey.from_user_text(f"  {KEY_TEXT}\r\n").reveal() == KEY_TEXT


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


def test_the_map_settings_default_to_four_gibibytes() -> None:
    assert MapSettings(has_key=False).cache_limit.bytes == DEFAULT_CACHE_BYTES == 4 * GIB
