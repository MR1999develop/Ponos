# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fakes of the map ports: tile source, key store and tile cache, and a generator of PNGs."""

from __future__ import annotations

import struct
import zlib
from collections import OrderedDict
from collections.abc import Sequence
from datetime import datetime, timedelta

from ponos_coach.maps.domain import TileAddress, TileKey, tile_style
from ponos_coach.maps.ports import (
    CachedTile,
    TileImage,
    TileKeyStoreCorruptError,
    TileKeyStoreUnavailableError,
    TileProblem,
    TileResult,
    TileUnavailable,
)

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


class InMemoryTileKeyStore:
    """The tile key in memory, with injectable failures."""

    def __init__(self, key: TileKey | None = None) -> None:
        self.key = key
        self.unavailable = False
        self.corrupt = False

    def load(self) -> TileKey | None:
        """See `TileKeyStore.load`."""
        if self.unavailable:
            raise TileKeyStoreUnavailableError("simulated")
        if self.corrupt:
            raise TileKeyStoreCorruptError("simulated")
        return self.key

    def save(self, key: TileKey) -> None:
        """See `TileKeyStore.save`."""
        if self.unavailable:
            raise TileKeyStoreUnavailableError("simulated")
        self.key = key
        self.corrupt = False

    def delete(self) -> None:
        """See `TileKeyStore.delete`."""
        if self.unavailable:
            raise TileKeyStoreUnavailableError("simulated")
        self.key = None
        self.corrupt = False


class FakeTileSource:
    """A tile source that follows a script.

    Without a script it behaves like the provider with a good key: no key saved gives `NO_KEY`,
    the key `ACCEPTED_KEY` gives a PNG, any other key is rejected. With `script`, each call takes
    the next result (the last one repeats). `calls` records `(z, x, y, style)` of every call.
    """

    ACCEPTED_KEY = "chiave-di-prova-valida"

    def __init__(
        self, keys: InMemoryTileKeyStore, script: Sequence[TileResult] | None = None
    ) -> None:
        self._keys = keys
        self._script = list(script) if script is not None else None
        self.calls: list[tuple[int, int, int, str]] = []
        self.png = make_png()
        self.ttl_s = 3600

    def fetch(self, z: int, x: int, y: int, language: str) -> TileResult:
        """See `TileSource.fetch`."""
        address = TileAddress(z, x, y)
        self.calls.append((address.z, address.x, address.y, tile_style(language)))
        if self._script is not None:
            index = min(len(self.calls), len(self._script)) - 1
            return self._script[index]
        key = self._keys.load()
        if key is None:
            return TileUnavailable(TileProblem.NO_KEY)
        if key.reveal() != self.ACCEPTED_KEY:
            return TileUnavailable(TileProblem.KEY_REJECTED)
        return TileImage(self.png, self.ttl_s)


class InMemoryTileCache:
    """Tiles in an ordered dictionary: exact cap on the PNG bytes, least recently used out."""

    def __init__(self, limit_bytes: int = 1_000_000) -> None:
        self._limit = limit_bytes
        self._tiles: OrderedDict[tuple[str, int, int, int], CachedTile] = OrderedDict()

    def get(self, style: str, address: TileAddress, now: datetime) -> CachedTile | None:
        """See `TileCache.get`."""
        del now
        key = (style, address.z, address.x, address.y)
        tile = self._tiles.get(key)
        if tile is not None:
            self._tiles.move_to_end(key)
        return tile

    def put(self, style: str, address: TileAddress, image: TileImage, now: datetime) -> None:
        """See `TileCache.put`."""
        if image.ttl_s == 0 or len(image.png) > self._limit:
            return
        key = (style, address.z, address.x, address.y)
        self._tiles.pop(key, None)
        self._tiles[key] = CachedTile(image.png, now + timedelta(seconds=image.ttl_s))
        self._evict()

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
