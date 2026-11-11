# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The map's lazy disk cache once the window is closed.

A tile request still on its way when the window closes used to open the cache file again; that
connection was never closed and the garbage collector found it later, in some other test (the
intermittent "unclosed database" of the Windows CI, 2026-11-09).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.__main__ import _LazyDiskTileCache
from ponos_coach.maps.domain import TileAddress
from ponos_coach.maps.ports import TileCacheError, TileImage
from ponos_coach.platform.tile_cache import CACHE_FILE_NAME
from tests.fakes.tiles import make_png

NOW = datetime(2026, 11, 9, tzinfo=UTC)


def test_after_close_a_late_request_does_not_open_the_file_again(tmp_path: Path) -> None:
    cache = _LazyDiskTileCache(tmp_path / "map", 10_000_000)
    cache.put(TileAddress(3, 1, 1), TileImage(make_png(), 60), NOW)
    cache.close()
    (tmp_path / "map" / CACHE_FILE_NAME).unlink()  # nothing may hold it any more

    with pytest.raises(TileCacheError):
        cache.get(TileAddress(3, 1, 1), NOW)  # the proxy takes it as a miss

    assert not (tmp_path / "map" / CACHE_FILE_NAME).exists()


def test_a_cache_never_used_opens_no_file(tmp_path: Path) -> None:
    cache = _LazyDiskTileCache(tmp_path / "map", 10_000_000)
    cache.close()

    with pytest.raises(TileCacheError):
        cache.used_bytes()

    assert not (tmp_path / "map").exists()
