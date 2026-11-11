# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the map background: tile source and tile cache.

The real implementations are in `platform` (HTTPS to tile.openstreetmap.org, SQLite cache in the
app's cache folder); fakes and contract tests are in `tests/`. No port records positions: a tile
address says which area is looked at, so it is never logged either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final, Protocol

from ponos_coach.maps.domain import TileAddress

MAX_TILE_TTL_S = 366 * 86_400  # a year: no provider should ask for more


class TileProblem(Enum):
    """Why a tile could not be had. The value is stable. The reaction differs for each."""

    REFUSED = "refused"  # 403 or 429: the server refuses or limits us; nothing to do for a while
    NETWORK_UNAVAILABLE = "network_unavailable"  # no network, timeout or server down: later
    INVALID_RESPONSE = "invalid_response"  # not a PNG, too big, redirect, unknown status
    # The server's certificate does not verify (expired, wrong name, unknown issuer): nothing is
    # taken from it, the check is never relaxed; only the server's owner can fix it.
    UNTRUSTED_SERVER = "untrusted_server"


@dataclass(frozen=True, slots=True)
class TileValidators:
    """What the server said about a copy of a tile, to ask "has it changed?" later.

    Both are optional opaque header values (printable ASCII, already checked by the adapter).
    """

    etag: str | None = None
    last_modified: str | None = None

    @property
    def is_empty(self) -> bool:
        """Whether there is nothing to make a request conditional on."""
        return self.etag is None and self.last_modified is None


NO_VALIDATORS: Final = TileValidators()


@dataclass(frozen=True, slots=True)
class TileImage:
    """A PNG tile, for how long the server lets it be reused (seconds, from the response) and
    the validators of that copy.

    Invariants: non-empty bytes, `ttl_s` between 0 and one year; 0 means "do not keep".
    """

    png: bytes
    ttl_s: int
    validators: TileValidators = NO_VALIDATORS

    def __post_init__(self) -> None:
        if not self.png or not 0 <= self.ttl_s <= MAX_TILE_TTL_S:
            raise ValueError("invalid tile image")


@dataclass(frozen=True, slots=True)
class TileNotModified:
    """The server confirmed the copy that was asked about (304): it can be used for `ttl_s`
    more seconds (from the headers of the 304)."""

    ttl_s: int

    def __post_init__(self) -> None:
        if not 0 <= self.ttl_s <= MAX_TILE_TTL_S:
            raise ValueError("invalid tile lifetime")


@dataclass(frozen=True, slots=True)
class TileUnavailable:
    """The tile could not be had, and why."""

    problem: TileProblem


type TileResult = TileImage | TileNotModified | TileUnavailable


class TileSource(Protocol):
    """Where tiles come from (the server, over HTTPS).

    One attempt per call, no retry: the caller decides whether and when to ask again. Never
    raises for network or server failures, which are `TileUnavailable`.
    """

    def fetch(
        self, z: int, x: int, y: int, validators: TileValidators = NO_VALIDATORS
    ) -> TileResult:
        """The tile at `z/x/y`. With non-empty `validators` the request is conditional and the
        server may answer `TileNotModified`; without them that answer is `INVALID_RESPONSE`.

        Raises:
            TileAddressError: `z`, `x` or `y` is not a tile; no request is made.
        """
        ...


class TileCacheError(Exception):
    """The tile cache cannot be read or written (it is derived data: it can be emptied)."""


@dataclass(frozen=True, slots=True)
class CachedTile:
    """A tile read from the cache, the instant after which it must be renewed and its validators."""

    png: bytes
    expires_at: datetime
    validators: TileValidators = NO_VALIDATORS

    def is_fresh(self, now: datetime) -> bool:
        """Whether it can still be served without asking the server."""
        return now < self.expires_at


class TileCache(Protocol):
    """Tiles already seen, with an exact byte cap and eviction of the least recently used.

    Only tiles that were asked for are stored; there is no prefetch. It is derived and
    regenerable: deleting it loses nothing but time and bandwidth.
    """

    def get(self, address: TileAddress, now: datetime) -> CachedTile | None:
        """The tile (fresh or expired) or `None`; a hit counts as a use for the eviction.

        Raises:
            TileCacheError: the cache cannot be read.
        """
        ...

    def put(self, address: TileAddress, image: TileImage, now: datetime) -> None:
        """Stores or replaces the tile with its validators, then evicts the least recently used
        until the cap holds.

        An image with `ttl_s == 0` is not stored. A tile larger than the whole cap is not stored.

        Raises:
            TileCacheError: the cache cannot be written.
        """
        ...

    def renew(self, address: TileAddress, ttl_s: int, now: datetime) -> None:
        """After a 304: the stored tile expires at `now + ttl_s` (0 means expired at once, so it
        is asked about again). No effect if the tile is not stored.

        Raises:
            TileCacheError: the cache cannot be written.
        """
        ...

    def clear(self) -> None:
        """Removes every tile; the size becomes zero.

        Raises:
            TileCacheError: the cache cannot be written.
        """
        ...

    def used_bytes(self) -> int:
        """Bytes the stored tiles are accounted for; never above the cap."""
        ...

    def set_limit(self, limit_bytes: int) -> None:
        """Changes the cap; if it is smaller, the least recently used tiles go at once.

        Raises:
            TileCacheError: the cache cannot be written.
        """
        ...
