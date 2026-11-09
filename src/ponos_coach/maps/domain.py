# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: tile address and cache limit.

Everything that comes from the map page (the path of a tile) or from the user (the cache size)
becomes one of these types here or is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

MIN_TILE_ZOOM: Final = 0
MAX_TILE_ZOOM: Final = 19  # highest zoom tile.openstreetmap.org serves
_MAX_PATH_CHARS: Final = 32  # "19/524287/524287.png" is 20: checked before the pattern
_PATH: Final = re.compile(r"/?([0-9]{1,2})/([0-9]{1,6})/([0-9]{1,6})(?:\.png)?")

MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB
MIN_CACHE_BYTES: Final = 64 * MIB
MAX_CACHE_BYTES: Final = 64 * GIB
DEFAULT_CACHE_BYTES: Final = 4 * GIB
_MAX_LIMIT_TEXT_CHARS: Final = 16
_LIMIT_TEXT: Final = re.compile(r"([0-9]{1,6})(?:[.,]([0-9]{1,3}))?")


class TileAddressError(ValueError):
    """The text or numbers are not the address of a tile (the input is never repeated)."""


@dataclass(frozen=True, slots=True)
class TileAddress:
    """A tile of the web-mercator pyramid: zoom `z` and column `x`, row `y` in `[0, 2^z)`."""

    z: int
    x: int
    y: int

    def __post_init__(self) -> None:
        values: tuple[object, ...] = (self.z, self.x, self.y)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            raise TileAddressError("tile coordinates must be integers")
        if not MIN_TILE_ZOOM <= self.z <= MAX_TILE_ZOOM:
            raise TileAddressError("tile zoom out of range")
        side = 1 << self.z
        if not (0 <= self.x < side and 0 <= self.y < side):
            raise TileAddressError("tile column or row out of range")


def parse_tile_path(path: str) -> TileAddress:
    """Tile of a path like `/z/x/y` or `z/x/y.png` (what the page asks of the `ponos://` scheme).

    Length first, then the pattern, then the ranges: digits only, no sign, no spaces, no
    extra segments.

    Raises:
        TileAddressError: anything else.
    """
    if len(path) > _MAX_PATH_CHARS:
        raise TileAddressError("tile path too long")
    match = _PATH.fullmatch(path)
    if match is None:
        raise TileAddressError("not a tile path")
    z, x, y = (int(group) for group in match.groups())
    return TileAddress(z, x, y)


@dataclass(frozen=True, slots=True)
class CacheLimit:
    """Exact cap of the tile cache, in bytes, between `MIN_CACHE_BYTES` and `MAX_CACHE_BYTES`."""

    bytes: int

    def __post_init__(self) -> None:
        if isinstance(self.bytes, bool) or not MIN_CACHE_BYTES <= self.bytes <= MAX_CACHE_BYTES:
            raise ValueError("cache size out of range")


def parse_cache_limit_gib(text: str) -> CacheLimit:
    """Cache size typed in gibibytes ("4", "0,5", "2.25"), comma or point as separator.

    Raises:
        ValueError: not a number, or outside `MIN_CACHE_BYTES` .. `MAX_CACHE_BYTES`.
    """
    body = text.strip()
    match = _LIMIT_TEXT.fullmatch(body) if len(body) <= _MAX_LIMIT_TEXT_CHARS else None
    if match is None:
        raise ValueError("not a size")
    gib = float(f"{match.group(1)}.{match.group(2) or '0'}")
    return CacheLimit(round(gib * GIB))
