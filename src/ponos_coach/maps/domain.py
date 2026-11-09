# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure types of the map background: tile address, style, key and settings.

Everything that comes from the map page (the path of a tile) or from the user (the key, the cache
size) becomes one of these types here or is rejected. The key is a secret: it is never shown,
serialised or compared in variable time.
"""

from __future__ import annotations

import hmac
import re
from dataclasses import dataclass
from typing import Final, NoReturn

MIN_TILE_ZOOM: Final = 0
MAX_TILE_ZOOM: Final = 19  # highest zoom the topographic style is asked for
_MAX_PATH_CHARS: Final = 32  # "19/524287/524287.png" is 20: checked before the pattern
_PATH: Final = re.compile(r"/?([0-9]{1,2})/([0-9]{1,6})/([0-9]{1,6})(?:\.png)?")

_LANGUAGE_CODE: Final = re.compile(r"([a-z]{2})(?:[-_][a-z0-9]{1,8})?")  # "it", "it_IT", "en-gb"
_LANGUAGES: Final = frozenset({"it", "en"})  # labels the topographic style offers
NEUTRAL_STYLE: Final = ""  # local names (map `topo_`), used when the language is not offered

_KEY: Final = re.compile(r"[A-Za-z0-9_-]{8,128}")  # URL-safe: it needs no escaping in a query

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


def tile_style(language: str) -> str:
    """Label style for a language code: `it` and `en` have their own, any other is `__`."""
    match = _LANGUAGE_CODE.fullmatch(language.strip().lower())
    return match.group(1) if match and match.group(1) in _LANGUAGES else NEUTRAL_STYLE


class TileKey:
    """The personal key of the tile provider, as an opaque secret.

    Printing it, converting it to text or formatting it gives `TileKey(<hidden>)`; the content
    comes out only from `reveal()`, called by the key store and by the HTTP adapter that builds the
    request. Not hashable, not serialisable, constant-time comparison. It is made only of URL-safe
    characters (letters, digits, `_` and `-`, 8 to 128), so it can be put in a query unchanged.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        """Raises:
        ValueError: not a string, or not 8 to 128 URL-safe characters. The text is not repeated.
        """
        candidate: object = value
        if not isinstance(candidate, str) or _KEY.fullmatch(candidate) is None:
            raise ValueError("invalid tile key")
        self._value = candidate

    @classmethod
    def from_user_text(cls, text: str) -> TileKey:
        """Key from what the user pasted: surrounding spaces and line breaks are ignored.

        Raises:
            ValueError: see `__init__`.
        """
        return cls(text.strip())

    def reveal(self) -> str:
        """The content: never to be logged, shown or put in an error."""
        return self._value

    def __repr__(self) -> str:
        return "TileKey(<hidden>)"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, TileKey):
            return NotImplemented
        return hmac.compare_digest(self._value.encode(), other._value.encode())

    __hash__ = None  # type: ignore[assignment]  # not hashable: a secret is not a dictionary key

    def __reduce__(self) -> NoReturn:
        raise TypeError("TileKey cannot be serialized")


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


@dataclass(frozen=True, slots=True)
class MapSettings:
    """What the settings page shows and the tile proxy uses.

    `has_key` tells whether a key is in the key store (the key itself never travels here);
    `cache_limit` is the cap of the tile cache.
    """

    has_key: bool
    cache_limit: CacheLimit = CacheLimit(DEFAULT_CACHE_BYTES)
