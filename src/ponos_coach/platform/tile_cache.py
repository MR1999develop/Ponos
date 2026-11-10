# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Disk cache of map tiles (`TileCache` port): one SQLite file, exact cap, least recently used out.

The cache lives in the app's cache folder (never in a profile folder, so a profile backup does not
carry the areas that were looked at), in a private folder like the others. Tiles are rows of one
table, keyed by background, zoom, column and row (a tile of one background never serves for
another, and one cap covers them all); every write is a transaction, so a tile is stored whole or
not at all. The cap is on the *accounted* size of the tiles: their bytes plus a per-row overhead,
rounded up to the SQLite page, so the database file stays within the cap (plus a few KiB of
schema). The WAL is truncated by a checkpoint after every write transaction, so the cap holds also
while the cache is open. A tile is evicted by order of use (a counter, not the clock), oldest
first, until the cap holds again. The cache is derived data: a file that cannot be read is deleted
and rebuilt empty.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from ponos_coach.maps.domain import TileAddress
from ponos_coach.maps.ports import CachedTile, TileCacheError, TileImage, TileValidators
from ponos_coach.platform.private_folder import make_private_directory

_LOG = logging.getLogger(__name__)

CACHE_FILE_NAME: Final = "tiles.sqlite3"
_SCHEMA_VERSION: Final = 3  # 1 had a label style column, 2 no source: old files are rebuilt
# PRAGMA takes no parameters: a literal that must match _SCHEMA_VERSION (test_tile_cache).
_SET_SCHEMA_VERSION: Final = "PRAGMA user_version = 3"
_PAGE_BYTES: Final = 4096
_ROW_OVERHEAD_BYTES: Final = 160  # key, index entry and row header, generous
_EVICTION_BATCH: Final = 64
_SIDE_FILES: Final = ("-wal", "-shm", "-journal")

_CREATE: Final = (
    """
    CREATE TABLE tile (
        id         INTEGER PRIMARY KEY,
        source     TEXT    NOT NULL CHECK (length(source) BETWEEN 1 AND 16),
        z          INTEGER NOT NULL CHECK (z BETWEEN 0 AND 20),
        x          INTEGER NOT NULL CHECK (x >= 0),
        y          INTEGER NOT NULL CHECK (y >= 0),
        expires_ms INTEGER NOT NULL,
        etag       TEXT    CHECK (etag IS NULL OR length(etag) BETWEEN 1 AND 256),
        modified   TEXT    CHECK (modified IS NULL OR length(modified) BETWEEN 1 AND 256),
        used_seq   INTEGER NOT NULL,
        cost       INTEGER NOT NULL CHECK (cost > 0),
        png        BLOB    NOT NULL,
        UNIQUE (source, z, x, y)
    ) STRICT
    """,
    "CREATE INDEX tile_by_use ON tile (used_seq)",
)


def accounted_bytes(png_size: int, validators_size: int = 0) -> int:
    """Bytes a tile counts against the cap: its size, its validators and the row overhead, in
    whole pages."""
    return -(-(png_size + validators_size + _ROW_OVERHEAD_BYTES) // _PAGE_BYTES) * _PAGE_BYTES


def _validators_size(validators: TileValidators) -> int:
    return len(validators.etag or "") + len(validators.last_modified or "")


def _key(address: TileAddress) -> tuple[str, int, int, int]:
    """What names a tile in the table: its background and its place in that background."""
    return (address.background.key, address.z, address.x, address.y)


def _ms(moment: datetime) -> int:
    return round(moment.timestamp() * 1000)


def _remove_database(path: Path) -> None:
    for candidate in (path, *(path.with_name(path.name + s) for s in _SIDE_FILES)):
        with contextlib.suppress(OSError):
            candidate.unlink(missing_ok=True)


def _ensure_private_folder(folder: Path) -> None:
    folder.parent.mkdir(parents=True, exist_ok=True)
    try:
        make_private_directory(folder)
    except FileExistsError:
        if not folder.is_dir():
            raise


def _require_usable(connection: sqlite3.Connection) -> None:
    """The file is a cache of this version and is intact.

    Raises:
        sqlite3.DatabaseError: it is not.
    """
    if connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
        raise sqlite3.DatabaseError("unknown cache version")
    if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise sqlite3.DatabaseError("cache file damaged")


class DiskTileCache:
    """`TileCache` on a SQLite file in `folder`. Thread-safe (one lock around the connection).

    Args:
        folder: the app's cache folder for the map (created private if missing).
        limit_bytes: the cap on the accounted size of the tiles; positive.
    """

    def __init__(self, folder: Path, limit_bytes: int) -> None:
        if limit_bytes <= 0:
            raise ValueError("the cache cap must be positive")
        _ensure_private_folder(folder)
        self._path = folder / CACHE_FILE_NAME
        self._limit = limit_bytes
        self._lock = threading.Lock()
        self._next_use = 0
        self._total = 0
        self._connection = self._open_or_rebuild()
        if self._total > self._limit:  # the cap may be lower than when the file was written
            with self._lock, self._failures(), self._transaction():
                self._evict()

    def close(self) -> None:
        """Closes the file (checkpoint included); after this the cache is not used."""
        with self._lock:
            self._connection.close()

    def used_bytes(self) -> int:
        """See `TileCache.used_bytes`."""
        with self._lock:
            return self._total

    def get(self, address: TileAddress, now: datetime) -> CachedTile | None:
        """See `TileCache.get`."""
        del now  # freshness is the caller's decision; the cache only keeps the expiry
        with self._lock, self._failures():
            row = self._connection.execute(
                "SELECT id, png, expires_ms, etag, modified FROM tile "
                "WHERE source = ? AND z = ? AND x = ? AND y = ?",
                _key(address),
            ).fetchone()
            if row is None:
                return None
            self._connection.execute(
                "UPDATE tile SET used_seq = ? WHERE id = ?", (self._take_use(), row[0])
            )
        return CachedTile(
            bytes(row[1]),
            datetime.fromtimestamp(row[2] / 1000, UTC),
            TileValidators(row[3], row[4]),
        )

    def put(self, address: TileAddress, image: TileImage, now: datetime) -> None:
        """See `TileCache.put`."""
        cost = accounted_bytes(len(image.png), _validators_size(image.validators))
        if image.ttl_s == 0 or cost > self._limit:
            return
        expires = _ms(now + timedelta(seconds=image.ttl_s))
        with self._lock, self._failures(), self._transaction():
            self._forget(address)
            self._connection.execute(
                "INSERT INTO tile "
                "(source, z, x, y, expires_ms, etag, modified, used_seq, cost, png) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*_key(address), expires, image.validators.etag,
                 image.validators.last_modified, self._take_use(), cost, image.png),
            )  # fmt: skip
            self._total += cost
            self._evict()

    def renew(self, address: TileAddress, ttl_s: int, now: datetime) -> None:
        """See `TileCache.renew`."""
        expires = _ms(now + timedelta(seconds=ttl_s))
        with self._lock, self._failures():
            self._connection.execute(
                "UPDATE tile SET expires_ms = ?, used_seq = ? "
                "WHERE source = ? AND z = ? AND x = ? AND y = ?",
                (expires, self._take_use(), *_key(address)),
            )

    def clear(self) -> None:
        """See `TileCache.clear`: the file is deleted and recreated, so the disk use is zero."""
        with self._lock:
            self._connection.close()
            _remove_database(self._path)
            try:
                self._connection = self._create()
            except sqlite3.Error as exc:
                raise TileCacheError("tile cache not recreated") from exc
            self._total = 0
            self._next_use = 0

    def set_limit(self, limit_bytes: int) -> None:
        """See `TileCache.set_limit`; a smaller cap evicts at once and returns the space."""
        if limit_bytes <= 0:
            raise ValueError("the cache cap must be positive")
        with self._lock, self._failures():
            self._limit = limit_bytes
            if self._total > limit_bytes:
                with self._transaction():
                    self._evict()
                self._connection.execute("PRAGMA incremental_vacuum").fetchall()

    # -- inside the lock ---------------------------------------------------------------

    def _take_use(self) -> int:
        self._next_use += 1
        return self._next_use

    def _forget(self, address: TileAddress) -> None:
        row = self._connection.execute(
            "SELECT id, cost FROM tile WHERE source = ? AND z = ? AND x = ? AND y = ?",
            _key(address),
        ).fetchone()
        if row is not None:
            self._connection.execute("DELETE FROM tile WHERE id = ?", (row[0],))
            self._total -= row[1]

    def _evict(self) -> None:
        """Removes the least recently used tiles until the accounted size fits the cap."""
        while self._total > self._limit:
            victims = self._connection.execute(
                "SELECT id, cost FROM tile ORDER BY used_seq LIMIT ?", (_EVICTION_BATCH,)
            ).fetchall()
            if not victims:  # nothing left to remove: the books are wrong, start them again
                self._total = 0
                return
            for tile_id, cost in victims:
                if self._total <= self._limit:
                    return
                self._connection.execute("DELETE FROM tile WHERE id = ?", (tile_id,))
                self._total -= cost

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        total_before = self._total
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            self._total = total_before
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        # The WAL is emptied after every write, so the file never grows beyond the cap
        # while the cache is open (plus a few KiB of schema).
        self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    @contextlib.contextmanager
    def _failures(self) -> Iterator[None]:
        try:
            yield
        except sqlite3.Error as exc:
            _LOG.warning("tile cache operation failed (%s)", type(exc).__name__)
            raise TileCacheError("tile cache operation failed") from exc

    # -- opening ------------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            str(self._path), timeout=5.0, isolation_level=None, check_same_thread=False
        )
        connection.execute("PRAGMA trusted_schema = OFF")
        return connection

    def _create(self) -> sqlite3.Connection:
        """A new empty cache file (the previous one, if any, is gone)."""
        connection = self._connect()
        try:
            connection.execute("PRAGMA auto_vacuum = INCREMENTAL")  # before the first table
            connection.execute("PRAGMA journal_mode = WAL").fetchall()
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _CREATE:
                connection.execute(statement)
            connection.execute(_SET_SCHEMA_VERSION)
            connection.execute("COMMIT")
        except BaseException:
            connection.close()
            raise
        return connection

    def _open_existing(self) -> sqlite3.Connection:
        connection = self._connect()
        try:
            _require_usable(connection)
            connection.execute("PRAGMA journal_mode = WAL").fetchall()
            connection.execute("PRAGMA synchronous = NORMAL")
            total, last_use = connection.execute(
                "SELECT COALESCE(SUM(cost), 0), COALESCE(MAX(used_seq), 0) FROM tile"
            ).fetchone()
        except BaseException:
            connection.close()
            raise
        self._total, self._next_use = int(total), int(last_use)
        return connection

    def _open_or_rebuild(self) -> sqlite3.Connection:
        try:
            if self._path.exists():
                try:
                    connection = self._open_existing()
                except sqlite3.Error as exc:  # derived data: start again from nothing
                    _LOG.warning("tile cache rebuilt (%s)", type(exc).__name__)
                    _remove_database(self._path)
                else:
                    return connection
            self._total = self._next_use = 0
            return self._create()
        except (sqlite3.Error, OSError) as exc:
            raise TileCacheError("tile cache cannot be opened") from exc
