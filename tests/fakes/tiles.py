# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fakes of the map ports: tile source and tile cache, and a generator of PNGs."""

from __future__ import annotations

import struct
import zlib
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime, timedelta

from ponos_coach.maps.domain import TileAddress
from ponos_coach.maps.ports import NO_VALIDATORS, CachedTile, TileImage, TileResult, TileValidators

_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, body: bytes) -> bytes:
    crc = zlib.crc32(kind + body)
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def make_png(width: int = 256, height: int = 256, *, extra_bytes: int = 0) -> bytes:
    """A valid PNG of the given size; `extra_bytes` of ancillary data make it larger."""
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grey
    row = b"\x00" + b"\x80" * width
    body = zlib.compress(row * height, 1)
    padding = _chunk(b"tEXt", b"k\x00" + b"x" * extra_bytes) if extra_bytes else b""
    return (
        _SIGNATURE
        + _chunk(b"IHDR", header)
        + padding
        + _chunk(b"IDAT", body)
        + _chunk(b"IEND", b"")
    )


class FakeTileSource:
    """A tile source that follows a script.

    Without a script every call gets a PNG (`png`, reusable for `ttl_s`). With `script`, each call
    takes the next result (the last one repeats). `calls` records `(z, x, y, validators)` of every
    call.
    """

    def __init__(self, script: Sequence[TileResult] | None = None) -> None:
        self._script = list(script) if script is not None else None
        self.calls: list[tuple[int, int, int, TileValidators]] = []
        self.png = make_png()
        self.ttl_s = 3600

    def fetch(
        self, z: int, x: int, y: int, validators: TileValidators = NO_VALIDATORS
    ) -> TileResult:
        """See `TileSource.fetch`."""
        address = TileAddress(z, x, y)
        self.calls.append((address.z, address.x, address.y, validators))
        if self._script is not None:
            index = min(len(self.calls), len(self._script)) - 1
            return self._script[index]
        return TileImage(self.png, self.ttl_s)


class InMemoryTileCache:
    """Tiles in an ordered dictionary: exact cap on the PNG bytes, least recently used out."""

    def __init__(self, limit_bytes: int = 1_000_000) -> None:
        self._limit = limit_bytes
        self._tiles: OrderedDict[tuple[int, int, int], CachedTile] = OrderedDict()

    def get(self, address: TileAddress, now: datetime) -> CachedTile | None:
        """See `TileCache.get`."""
        del now
        key = (address.z, address.x, address.y)
        tile = self._tiles.get(key)
        if tile is not None:
            self._tiles.move_to_end(key)
        return tile

    def put(self, address: TileAddress, image: TileImage, now: datetime) -> None:
        """See `TileCache.put`."""
        if image.ttl_s == 0 or len(image.png) > self._limit:
            return
        key = (address.z, address.x, address.y)
        self._tiles.pop(key, None)
        self._tiles[key] = CachedTile(
            image.png, now + timedelta(seconds=image.ttl_s), image.validators
        )
        self._evict()

    def renew(self, address: TileAddress, ttl_s: int, now: datetime) -> None:
        """See `TileCache.renew`."""
        key = (address.z, address.x, address.y)
        tile = self._tiles.get(key)
        if tile is not None:
            self._tiles[key] = CachedTile(tile.png, now + timedelta(seconds=ttl_s), tile.validators)
            self._tiles.move_to_end(key)

    def _evict(self) -> None:
        while self.used_bytes() > self._limit:
            self._tiles.popitem(last=False)

    def clear(self) -> None:
        """See `TileCache.clear`."""
        self._tiles.clear()

    def used_bytes(self) -> int:
        """See `TileCache.used_bytes`: here the PNG bytes."""
        return sum(len(t.png) for t in self._tiles.values())

    def set_limit(self, limit_bytes: int) -> None:
        """See `TileCache.set_limit`."""
        self._limit = limit_bytes
        self._evict()
