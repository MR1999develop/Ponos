# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Disk tile cache: contract, exact cap on disk, persistence, damage, threads (T-MAP-11)."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ponos_coach.maps.domain import TileAddress
from ponos_coach.maps.ports import TileCache, TileCacheError, TileImage
from ponos_coach.platform.tile_cache import CACHE_FILE_NAME, DiskTileCache, accounted_bytes
from tests.contracts.contract_tiles import STYLE, TileCacheContract, png_of
from tests.fakes.tiles import InMemoryTileCache

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


class TestInMemoryTileCache(TileCacheContract):
    @pytest.fixture
    def cache(self) -> TileCache:
        return InMemoryTileCache()


class TestDiskTileCache(TileCacheContract):
    @pytest.fixture
    def cache(self, tmp_path: Path) -> Iterator[TileCache]:
        disk = DiskTileCache(tmp_path / "maps", 1_000_000)
        yield disk
        disk.close()


def disk_usage(folder: Path) -> int:
    return sum(p.stat().st_size for p in folder.iterdir() if p.is_file())


def test_t_map_11_the_files_on_disk_stay_within_the_cap(tmp_path: Path) -> None:
    folder, cap = tmp_path / "maps", 300_000
    cache = DiskTileCache(folder, cap)
    try:
        for number in range(200):
            cache.put(STYLE, TileAddress(12, number, 7), TileImage(png_of(9000), 60), NOW)
            assert cache.used_bytes() <= cap
        cache.set_limit(cap)  # nothing to do, nothing breaks
    finally:
        cache.close()

    # after closing (checkpoint) the one file is the whole cache: schema and pages included
    assert disk_usage(folder) <= cap + 64 * 1024


def test_the_accounting_is_in_whole_pages_with_a_row_overhead() -> None:
    assert accounted_bytes(1) == 4096
    assert accounted_bytes(4096 - 160) == 4096
    assert accounted_bytes(4096 - 159) == 8192
    assert accounted_bytes(100_000) % 4096 == 0
    assert accounted_bytes(100_000) > 100_000


def test_tiles_survive_closing_and_reopening_with_their_order_of_use(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    cache = DiskTileCache(folder, 40_000)
    for n in range(3):
        cache.put(STYLE, TileAddress(10, n, 1), TileImage(png_of(9000), 60), NOW)
    assert cache.get(STYLE, TileAddress(10, 0, 1), NOW) is not None  # tile 0 is the newest use
    used = cache.used_bytes()
    cache.close()

    reopened = DiskTileCache(folder, 40_000)
    try:
        assert reopened.used_bytes() == used
        reopened.put(STYLE, TileAddress(10, 3, 1), TileImage(png_of(9000), 60), NOW)
        reopened.put(STYLE, TileAddress(10, 4, 1), TileImage(png_of(9000), 60), NOW)
        assert reopened.get(STYLE, TileAddress(10, 1, 1), NOW) is None  # least used went first
        assert reopened.get(STYLE, TileAddress(10, 0, 1), NOW) is not None
    finally:
        reopened.close()


def test_a_smaller_cap_at_opening_trims_the_old_file(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    cache = DiskTileCache(folder, 500_000)
    for n in range(20):
        cache.put(STYLE, TileAddress(10, n, 1), TileImage(png_of(9000), 60), NOW)
    cache.close()

    smaller = DiskTileCache(folder, 50_000)
    try:
        assert 0 < smaller.used_bytes() <= 50_000
        assert smaller.get(STYLE, TileAddress(10, 19, 1), NOW) is not None
    finally:
        smaller.close()


def test_clear_leaves_no_tile_bytes_on_disk(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    cache = DiskTileCache(folder, 1_000_000)
    try:
        for n in range(30):
            cache.put(STYLE, TileAddress(10, n, 1), TileImage(png_of(9000), 60), NOW)
        assert disk_usage(folder) > 100_000

        cache.clear()

        assert cache.used_bytes() == 0
        assert disk_usage(folder) < 100_000  # an empty database, nothing of the tiles
    finally:
        cache.close()


@pytest.mark.parametrize(
    "damage",
    [b"", b"not a database at all" * 100, b"SQLite format 3\x00" + b"\xff" * 5000],
    ids=["empty", "garbage", "bad-pages"],
)
def test_a_damaged_cache_file_is_rebuilt_empty_not_an_error(tmp_path: Path, damage: bytes) -> None:
    folder = tmp_path / "maps"
    folder.mkdir()
    (folder / CACHE_FILE_NAME).write_bytes(damage)

    cache = DiskTileCache(folder, 100_000)
    try:
        assert cache.used_bytes() == 0
        cache.put(STYLE, TileAddress(10, 1, 1), TileImage(png_of(9000), 60), NOW)
        assert cache.get(STYLE, TileAddress(10, 1, 1), NOW) is not None
    finally:
        cache.close()


def test_a_cache_of_another_version_is_rebuilt(tmp_path: Path) -> None:
    folder = tmp_path / "maps"
    cache = DiskTileCache(folder, 100_000)
    cache.put(STYLE, TileAddress(10, 1, 1), TileImage(png_of(9000), 60), NOW)
    cache.close()
    with sqlite3.connect(folder / CACHE_FILE_NAME) as raw:
        raw.execute("PRAGMA user_version = 99")
    raw.close()

    rebuilt = DiskTileCache(folder, 100_000)
    try:
        assert rebuilt.used_bytes() == 0
    finally:
        rebuilt.close()


def test_the_cache_folder_is_created_when_missing_and_reused_when_present(tmp_path: Path) -> None:
    folder = tmp_path / "deep" / "er" / "maps"

    DiskTileCache(folder, 100_000).close()
    DiskTileCache(folder, 100_000).close()

    assert (folder / CACHE_FILE_NAME).is_file()


def test_a_path_that_is_a_file_is_an_error_not_a_silent_fallback(tmp_path: Path) -> None:
    blocker = tmp_path / "maps"
    blocker.write_bytes(b"x")

    with pytest.raises(OSError):
        DiskTileCache(blocker, 100_000)


@pytest.mark.parametrize("limit", [0, -1])
def test_a_cap_that_is_not_positive_is_rejected(tmp_path: Path, limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        DiskTileCache(tmp_path / "maps", limit)
    cache = DiskTileCache(tmp_path / "maps", 100_000)
    try:
        with pytest.raises(ValueError, match="positive"):
            cache.set_limit(limit)
    finally:
        cache.close()


def test_many_threads_asking_and_storing_keep_the_books_right(tmp_path: Path) -> None:
    cap = 100_000
    cache = DiskTileCache(tmp_path / "maps", cap)
    errors: list[BaseException] = []

    def worker(offset: int) -> None:
        try:
            for n in range(60):
                address = TileAddress(12, offset * 100 + n, 3)
                cache.put(STYLE, address, TileImage(png_of(6000), 60), NOW)
                cache.get(STYLE, address, NOW)
                assert cache.used_bytes() <= cap
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    try:
        assert not errors
        assert not any(t.is_alive() for t in threads)
        stored = cache.used_bytes()
    finally:
        cache.close()
    reopened = DiskTileCache(tmp_path / "maps", cap)
    try:
        assert reopened.used_bytes() == stored  # the books matched the file
    finally:
        reopened.close()


def test_a_closed_cache_reports_an_error_instead_of_crashing(tmp_path: Path) -> None:
    cache = DiskTileCache(tmp_path / "maps", 100_000)
    cache.close()

    with pytest.raises(TileCacheError):
        cache.get(STYLE, TileAddress(1, 0, 0), NOW)
    with pytest.raises(TileCacheError):
        cache.put(STYLE, TileAddress(1, 0, 0), TileImage(png_of(3000), 60), NOW)
