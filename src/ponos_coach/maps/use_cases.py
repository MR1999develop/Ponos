# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use case of the map background: `TileProxy` (a tile for the page).

`TileProxy` is what the `ponos://tiles/{z}/{x}/{y}` handler calls. It asks the cache first, then
the server (conditionally, if it holds an expired copy), one attempt at a time, and after a failure
it stops asking for a while (a cool-down per problem: global when the server refuses or limits us
or the network is down; for one tile only after a bad answer about it) so that a page that wants
dozens of tiles cannot hammer a server that said no. It never prefetches: only the tiles the page
asks for. No log: a tile address says which area is being looked at.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from ponos_coach.maps.domain import TileAddress, parse_tile_path
from ponos_coach.maps.ports import (
    NO_VALIDATORS,
    CachedTile,
    TileCache,
    TileCacheError,
    TileImage,
    TileNotModified,
    TileProblem,
    TileSource,
    TileUnavailable,
)

# Seconds without asking the server again after each problem. A refusal or a rate limit does not
# fix itself in seconds; a flaky network may.
COOLDOWN_S: Final[dict[TileProblem, float]] = {
    TileProblem.REFUSED: 300.0,
    TileProblem.NETWORK_UNAVAILABLE: 15.0,
    TileProblem.INVALID_RESPONSE: 15.0,
}
# Problems about one tile (a 404, a body that is not a PNG): the cool-down is for that tile only.
_PER_TILE: Final = frozenset({TileProblem.INVALID_RESPONSE})
_MAX_TILE_BLOCKS: Final = 512  # bound of the per-tile table: the oldest is dropped


@dataclass(frozen=True, slots=True)
class TileAnswer:
    """What the page gets for a tile.

    `png` is the image, if there is one; `problem` is why the server could not be reached or
    refused, if so. Both can be present (an expired tile from the cache served because the
    server cannot be asked): the page shows the tile and the warning. At least one is set.
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
        self._tile_blocks: dict[TileAddress, float] = {}  # tile -> monotonic end

    def serve(self, path: str) -> TileAnswer:
        """Tile for a path as the page writes it (`/z/x/y`, `z/x/y.png`).

        Raises:
            TileAddressError: the path is not a tile; nothing is asked of cache or server.
        """
        return self.tile(parse_tile_path(path))

    def tile(self, address: TileAddress) -> TileAnswer:
        """The tile at the address: fresh from the cache, else from the server (with the
        validators of an expired copy, so a 304 renews it instead of downloading it again)."""
        now = self._now()
        cached = self._cached(address, now)
        if cached is not None and cached.is_fresh(now):
            return TileAnswer(cached.png, None)
        problem = self._current_problem()
        if problem is None and self._tile_is_blocked(address):
            problem = TileProblem.INVALID_RESPONSE
        elif problem is None:
            return self._ask(address, cached, now)
        return TileAnswer(None if cached is None else cached.png, problem)

    def clear_cache(self) -> None:
        """Empties the tile cache ("Clear map cache").

        Raises:
            TileCacheError: the cache cannot be written.
        """
        self._cache.clear()

    def _ask(self, address: TileAddress, cached: CachedTile | None, now: datetime) -> TileAnswer:
        validators = NO_VALIDATORS if cached is None else cached.validators
        result = self._source.fetch(address.z, address.x, address.y, validators)
        if isinstance(result, TileImage):
            self._store(address, result, now)
            return TileAnswer(result.png, None)
        if isinstance(result, TileNotModified) and cached is not None:
            self._renew(address, result.ttl_s, now)
            return TileAnswer(cached.png, None)
        failure = (
            result
            if isinstance(result, TileUnavailable)
            else TileUnavailable(TileProblem.INVALID_RESPONSE)  # a 304 nobody asked for
        )
        problem = self._block(failure, address)
        return TileAnswer(None if cached is None else cached.png, problem)

    def _cached(self, address: TileAddress, now: datetime) -> CachedTile | None:
        try:
            return self._cache.get(address, now)
        except TileCacheError:  # derived data: a damaged cache is a miss (the adapter logs it)
            return None

    def _store(self, address: TileAddress, image: TileImage, now: datetime) -> None:
        try:
            self._cache.put(address, image, now)
        except TileCacheError:  # the tile is served anyway
            return

    def _renew(self, address: TileAddress, ttl_s: int, now: datetime) -> None:
        try:
            self._cache.renew(address, ttl_s, now)
        except TileCacheError:  # the copy is served anyway; it will be asked about again
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

    def _tile_is_blocked(self, address: TileAddress) -> bool:
        with self._lock:
            until = self._tile_blocks.get(address)
            if until is None:
                return False
            if self._monotonic() < until:
                return True
            del self._tile_blocks[address]
            return False

    def _block(self, failure: TileUnavailable, address: TileAddress) -> TileProblem:
        seconds = COOLDOWN_S[failure.problem]
        if seconds <= 0:
            return failure.problem
        end = self._monotonic() + seconds
        with self._lock:
            if failure.problem in _PER_TILE:
                if len(self._tile_blocks) >= _MAX_TILE_BLOCKS:
                    del self._tile_blocks[next(iter(self._tile_blocks))]
                self._tile_blocks[address] = end
            else:
                self._blocked = (failure.problem, end)
        return failure.problem
