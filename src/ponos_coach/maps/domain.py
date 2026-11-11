# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: the backgrounds, tile address and cache limit.

Everything that comes from the map page (the path of a tile) or from the user (the cache size)
becomes one of these types here or is rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

MIN_TILE_ZOOM: Final = 0
MAX_TILE_ZOOM: Final = 20  # highest zoom of any background; each one has its own, lower or equal
_MAX_PATH_CHARS: Final = 64  # "cyclosm/20/1048575/1048575.png" is 30: checked before the pattern
_PATH: Final = re.compile(r"/?([a-z]{1,16})/([0-9]{1,2})/([0-9]{1,7})/([0-9]{1,7})(?:\.png)?")
_KEY: Final = re.compile(r"[a-z]{1,16}")
_HOST: Final = re.compile(r"(?:\{s\}\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)+")


class TileAddressError(ValueError):
    """The text or numbers are not the address of a tile (the input is never repeated)."""


@dataclass(frozen=True, slots=True)
class MapBackground:
    """A map background the app may ask tiles of: the closed set `BACKGROUNDS`.

    `key` is the stable name used in the configuration and in the `ponos://tiles/{key}/z/x/y`
    path; `label` is the proper name shown to the user; `host` is the tile server (`{s}` stands
    for one of `subdomains`, if there are any); `path_prefix` comes before `/z/x/y.png`;
    `max_zoom` is the highest zoom the server has; `attribution` is the credit the data
    licence asks for. Nothing here comes from outside the program: a background is never built
    from a URL typed by the user or read from a setting (the key is looked up in the set).
    """

    key: str
    label: str
    host: str
    subdomains: tuple[str, ...]
    path_prefix: str
    max_zoom: int
    attribution: str

    def __post_init__(self) -> None:
        has_placeholder = "{s}" in self.host
        if (
            _KEY.fullmatch(self.key) is None
            or _HOST.fullmatch(self.host) is None
            or has_placeholder != bool(self.subdomains)
            or not all(re.fullmatch(r"[a-z0-9]{1,8}", s) for s in self.subdomains)
            or re.fullmatch(r"(?:/[a-z0-9-]{1,32})?", self.path_prefix) is None
            or not MIN_TILE_ZOOM <= self.max_zoom <= MAX_TILE_ZOOM
            or not self.label
            or not self.attribution
        ):
            raise ValueError("invalid map background")

    def host_for(self, address: TileAddress) -> str:
        """The server host for a tile; with subdomains, always the same one for the same tile."""
        if not self.subdomains:
            return self.host
        return self.host.replace(
            "{s}", self.subdomains[(address.x + address.y) % len(self.subdomains)]
        )

    def path_for(self, address: TileAddress) -> str:
        """The request path of a tile on the server."""
        return f"{self.path_prefix}/{address.z}/{address.x}/{address.y}.png"

    def url_for(self, address: TileAddress) -> str:
        """The full HTTPS URL of a tile (for tests and documentation: it says what is asked)."""
        return f"https://{self.host_for(address)}{self.path_for(address)}"


OPENSTREETMAP: Final = MapBackground(
    key="openstreetmap",
    label="OpenStreetMap",
    host="tile.openstreetmap.org",
    subdomains=(),
    path_prefix="",
    max_zoom=19,
    attribution="© OpenStreetMap contributors",
)
OPENTOPOMAP: Final = MapBackground(
    key="opentopomap",
    label="OpenTopoMap",
    host="{s}.tile.opentopomap.org",
    subdomains=("a", "b", "c"),
    path_prefix="",
    max_zoom=17,
    attribution=(
        "Map data: © OpenStreetMap contributors, SRTM | Map style: © OpenTopoMap (CC-BY-SA)"
    ),
)
CYCLOSM: Final = MapBackground(
    key="cyclosm",
    label="CyclOSM",
    host="{s}.tile-cyclosm.openstreetmap.fr",
    subdomains=("a", "b", "c"),
    path_prefix="/cyclosm",
    max_zoom=20,
    attribution="CyclOSM | Map data: © OpenStreetMap contributors",
)
BACKGROUNDS: Final = (OPENSTREETMAP, OPENTOPOMAP, CYCLOSM)  # the closed set, in menu order
DEFAULT_BACKGROUND: Final = OPENSTREETMAP


def background_by_key(key: object) -> MapBackground:
    """The background with this stable key.

    Raises:
        TileAddressError: no background has that key (the input is not repeated).
    """
    for background in BACKGROUNDS:
        if background.key == key:
            return background
    raise TileAddressError("unknown map background")


@dataclass(frozen=True, slots=True)
class TileAddress:
    """A tile of the web-mercator pyramid of one background: zoom `z`, column `x` and row `y` in
    `[0, 2^z)`, with `z` up to the background's own maximum."""

    z: int
    x: int
    y: int
    background: MapBackground = DEFAULT_BACKGROUND

    def __post_init__(self) -> None:
        values: tuple[object, ...] = (self.z, self.x, self.y)
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
            raise TileAddressError("tile coordinates must be integers")
        if not MIN_TILE_ZOOM <= self.z <= self.background.max_zoom:
            raise TileAddressError("tile zoom out of range")
        side = 1 << self.z
        if not (0 <= self.x < side and 0 <= self.y < side):
            raise TileAddressError("tile column or row out of range")


def parse_tile_path(path: str) -> TileAddress:
    """Tile of a path like `/source/z/x/y` or `source/z/x/y.png` (what the page asks of the
    `ponos://` scheme), the source being the key of one of `BACKGROUNDS`.

    Length first, then the pattern, then the source and the ranges: digits only, no sign, no
    spaces, no extra segments.

    Raises:
        TileAddressError: anything else, an unknown source included.
    """
    if len(path) > _MAX_PATH_CHARS:
        raise TileAddressError("tile path too long")
    match = _PATH.fullmatch(path)
    if match is None:
        raise TileAddressError("not a tile path")
    z, x, y = (int(group) for group in match.groups()[1:])
    return TileAddress(z, x, y, background_by_key(match.group(1)))


MIB: Final = 1024 * 1024
GIB: Final = 1024 * MIB
MIN_CACHE_BYTES: Final = 64 * MIB
MAX_CACHE_BYTES: Final = 64 * GIB
DEFAULT_CACHE_BYTES: Final = 4 * GIB
_MAX_LIMIT_TEXT_CHARS: Final = 16
_LIMIT_TEXT: Final = re.compile(r"([0-9]{1,6})(?:[.,]([0-9]{1,3}))?")


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
