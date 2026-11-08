# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the map background: tile source, key store and tile cache.

The real implementations are in `platform` (HTTPS to the provider, system key store, SQLite cache
in the app's cache folder); fakes and contract tests are in `tests/`. No port records positions:
a tile address says which area is looked at, so it is never logged either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol

from ponos_coach.maps.domain import TileAddress, TileKey

MAX_TILE_TTL_S = 366 * 86_400  # a year: no provider should ask for more


class TileProblem(Enum):
    """Why a tile could not be had. The value is stable. The reaction differs for each."""

    NO_KEY = "no_key"  # no key saved: ask for one
    KEY_REJECTED = "key_rejected"  # the provider refuses it: ask for a new one
    NETWORK_UNAVAILABLE = "network_unavailable"  # no network, timeout or provider down: later
    CREDIT_EXHAUSTED = "credit_exhausted"  # quota or credit over: nothing to do now
    INVALID_RESPONSE = "invalid_response"  # not a PNG, too big, redirect, unknown status


@dataclass(frozen=True, slots=True)
class TileImage:
    """A PNG tile and for how long the provider lets it be reused (seconds, from the response).

    Invariants: non-empty bytes, `ttl_s` between 0 and one year; 0 means "do not keep".
    """

    png: bytes
    ttl_s: int

    def __post_init__(self) -> None:
        if not self.png or not 0 <= self.ttl_s <= MAX_TILE_TTL_S:
            raise ValueError("invalid tile image")


@dataclass(frozen=True, slots=True)
class TileUnavailable:
    """The tile could not be had, and why."""

    problem: TileProblem


type TileResult = TileImage | TileUnavailable


class TileSource(Protocol):
    """Where tiles come from (the provider, over HTTPS).

    One attempt per call, no retry: the caller decides whether and when to ask again. Never
    raises for network or provider failures, which are `TileUnavailable`.
    """

    def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:
        """The tile at `z/x/y` with labels in `language` (a code like `it`, `en`; any other gives
        the provider's default names).

        Raises:
            TileAddressError: `z`, `x` or `y` is not a tile; no request is made.
        """
        ...


class TileKeyStoreError(Exception):
    """Root of the key store errors."""


class TileKeyStoreUnavailableError(TileKeyStoreError):
    """The system key store is absent, locked or not secure: the key is not read or saved."""


class TileKeyStoreCorruptError(TileKeyStoreError):
    """What is saved is not a valid key."""


class TileKeyStore(Protocol):
    """The tile key in the system key store, outside the profile folder and its backups."""

    def load(self) -> TileKey | None:
        """The key, or `None` if none is saved.

        Raises:
            TileKeyStoreUnavailableError: the store cannot be read.
            TileKeyStoreCorruptError: what is saved is not a valid key.
        """
        ...

    def save(self, key: TileKey) -> None:
        """Saves the key, replacing any previous one.

        Raises:
            TileKeyStoreUnavailableError: the store cannot be written.
        """
        ...

    def delete(self) -> None:
        """Forgets the key (no effect if none is saved).

        Raises:
            TileKeyStoreUnavailableError: the store cannot be written.
        """
        ...


class TileCacheError(Exception):
    """The tile cache cannot be read or written (it is derived data: it can be emptied)."""


@dataclass(frozen=True, slots=True)
class CachedTile:
    """A tile read from the cache and the instant after which it must be renewed."""

    png: bytes
    expires_at: datetime

    def is_fresh(self, now: datetime) -> bool:
        """Whether it can still be served without asking the provider."""
        return now < self.expires_at


class TileCache(Protocol):
    """Tiles already seen, with an exact byte cap and eviction of the least recently used.

    Only tiles that were asked for are stored; there is no prefetch. It is derived and
    regenerable: deleting it loses nothing but time and credit.
    """

    def get(self, style: str, address: TileAddress, now: datetime) -> CachedTile | None:
        """The tile (fresh or expired) or `None`; a hit counts as a use for the eviction.

        Raises:
            TileCacheError: the cache cannot be read.
        """
        ...

    def put(self, style: str, address: TileAddress, image: TileImage, now: datetime) -> None:
        """Stores or replaces the tile, then evicts the least recently used until the cap holds.

        An image with `ttl_s == 0` is not stored. A tile larger than the whole cap is not stored.

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
