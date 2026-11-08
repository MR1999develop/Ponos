# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the map background: `TileProxy` (a tile for the page) and `TileKeySettings`.

`TileProxy` is what the `ponos://tiles/{z}/{x}/{y}` handler calls. It asks the cache first, then
the provider, one attempt at a time, and after a failure it stops asking for a while (a cool-down
per problem) so that a page that wants dozens of tiles cannot hammer a provider that refused the
key or ran out of credit. It never prefetches: only the tiles the page asks for. No log: a tile
address says which area is being looked at.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final

from ponos_coach.maps.domain import (
    CacheLimit,
    MapSettings,
    TileAddress,
    TileKey,
    parse_tile_path,
    tile_style,
)
from ponos_coach.maps.ports import (
    CachedTile,
    TileCache,
    TileCacheError,
    TileImage,
    TileKeyStore,
    TileKeyStoreCorruptError,
    TileProblem,
    TileSource,
    TileUnavailable,
)

# Seconds without asking the provider again after each problem (project default). A key that
# was refused or a credit that is over do not fix themselves in seconds; a flaky network may.
COOLDOWN_S: Final[dict[TileProblem, float]] = {
    TileProblem.NO_KEY: 0.0,  # nothing is asked of the network without a key anyway
    TileProblem.KEY_REJECTED: 300.0,
    TileProblem.CREDIT_EXHAUSTED: 300.0,
    TileProblem.NETWORK_UNAVAILABLE: 15.0,
    TileProblem.INVALID_RESPONSE: 15.0,
}
_CHECK_ADDRESS: Final = TileAddress(0, 0, 0)  # the whole world: the cheapest tile to ask for


@dataclass(frozen=True, slots=True)
class TileAnswer:
    """What the page gets for a tile.

    `png` is the image, if there is one; `problem` is why the provider could not be reached or
    refused, if so. Both can be present (an expired tile from the cache served because the
    provider cannot be asked): the page shows the tile and the warning. At least one is set.
    """

    png: bytes | None
    problem: TileProblem | None

    def __post_init__(self) -> None:
        if self.png is None and self.problem is None:
            raise ValueError("an answer has a tile, a problem or both")


class TileProxy:
    """Use case "get a map tile": cache, then provider, with cool-downs. Thread-safe.

    Dependencies: tile source, cache, the UTC clock and a monotonic clock (for the cool-downs),
    both injected.
    """

    def __init__(
        self,
        source: TileSource,
        cache: TileCache,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
    ) -> None:
        self._source = source
        self._cache = cache
        self._now = now
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._blocked: tuple[TileProblem, float] | None = None  # problem, monotonic end

    def serve(self, path: str, language: str) -> TileAnswer:
        """Tile for a path as the page writes it (`/z/x/y`, `z/x/y.png`).

        Raises:
            TileAddressError: the path is not a tile; nothing is asked of cache or provider.
        """
        return self.tile(parse_tile_path(path), language)

    def tile(self, address: TileAddress, language: str) -> TileAnswer:
        """The tile at the address with labels in `language`."""
        style = tile_style(language)
        now = self._now()
        cached = self._cached(style, address, now)
        if cached is not None and cached.is_fresh(now):
            return TileAnswer(cached.png, None)
        problem = self._current_problem()
        if problem is None:
            result = self._source.fetch(address.z, address.x, address.y, style)
            if isinstance(result, TileImage):
                self._store(style, address, result, now)
                return TileAnswer(result.png, None)
            problem = self._block(result)
        return TileAnswer(None if cached is None else cached.png, problem)

    def key_changed(self) -> None:
        """The key was replaced or removed: the cool-down no longer applies."""
        with self._lock:
            self._blocked = None

    def clear_cache(self) -> None:
        """Empties the tile cache ("Clear map cache").

        Raises:
            TileCacheError: the cache cannot be written.
        """
        self._cache.clear()

    def _cached(self, style: str, address: TileAddress, now: datetime) -> CachedTile | None:
        try:
            return self._cache.get(style, address, now)
        except TileCacheError:  # derived data: a damaged cache is a miss (the adapter logs it)
            return None

    def _store(self, style: str, address: TileAddress, image: TileImage, now: datetime) -> None:
        try:
            self._cache.put(style, address, image, now)
        except TileCacheError:  # the tile is served anyway
            return

    def _current_problem(self) -> TileProblem | None:
        with self._lock:
            if self._blocked is None:
                return None
            problem, until = self._blocked
            if self._monotonic() < until:
                return problem
            self._blocked = None
            return None

    def _block(self, failure: TileUnavailable) -> TileProblem:
        seconds = COOLDOWN_S[failure.problem]
        if seconds > 0:
            with self._lock:
                self._blocked = (failure.problem, self._monotonic() + seconds)
        return failure.problem


class KeyCheck(Enum):
    """Outcome of saving a key. The reaction differs for each."""

    ACCEPTED = "accepted"  # the provider served a tile with it
    REJECTED = "rejected"  # the provider refuses it; the previous key (if any) is back
    INVALID_FORMAT = "invalid_format"  # not a key; nothing was saved
    SAVED_UNVERIFIED = "saved_unverified"  # saved, but the provider could not be asked (or the
    # credit is over): say so, the map will try when it can


class TileKeySettings:
    """Use cases "set the tile key", "remove it" and "read the map settings"."""

    def __init__(self, store: TileKeyStore, source: TileSource, proxy: TileProxy) -> None:
        self._store = store
        self._source = source
        self._proxy = proxy

    def set_key(self, text: str) -> KeyCheck:
        """Saves the key the user typed after checking it with the provider.

        A refused key is not kept: the previous one (if there was one) stays. The text is never
        repeated in the outcome.

        Raises:
            TileKeyStoreUnavailableError: the system key store cannot be used.
        """
        try:
            key = TileKey.from_user_text(text)
        except ValueError:
            return KeyCheck.INVALID_FORMAT
        previous = self._previous_key()
        self._store.save(key)
        result = self._source.fetch(_CHECK_ADDRESS.z, _CHECK_ADDRESS.x, _CHECK_ADDRESS.y, "en")
        self._proxy.key_changed()
        if isinstance(result, TileImage):
            return KeyCheck.ACCEPTED
        if result.problem is TileProblem.KEY_REJECTED:
            self._restore(previous)
            return KeyCheck.REJECTED
        return KeyCheck.SAVED_UNVERIFIED

    def remove_key(self) -> None:
        """Forgets the key; the map goes back to the neutral background.

        Raises:
            TileKeyStoreUnavailableError: the system key store cannot be used.
        """
        self._store.delete()
        self._proxy.key_changed()

    def settings(self, cache_limit: CacheLimit) -> MapSettings:
        """The map settings with the cap chosen by the user.

        Raises:
            TileKeyStoreUnavailableError: the system key store cannot be read.
        """
        try:
            has_key = self._store.load() is not None
        except TileKeyStoreCorruptError:
            has_key = False  # what is saved is not a key: for the user there is none
        return MapSettings(has_key, cache_limit)

    def _previous_key(self) -> TileKey | None:
        try:
            return self._store.load()
        except TileKeyStoreCorruptError:
            return None

    def _restore(self, previous: TileKey | None) -> None:
        if previous is None:
            self._store.delete()
        else:
            self._store.save(previous)
        self._proxy.key_changed()
