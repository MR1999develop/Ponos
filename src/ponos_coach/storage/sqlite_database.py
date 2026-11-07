# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite connection of a profile: creation, opening, migrations and transactions.

Durability choices: `foreign_keys = ON` and `trusted_schema = OFF` on every connection;
`journal_mode = WAL` with `synchronous = FULL` (the WAL write is flushed to disk on every
commit, so a confirmed activity survives a power failure); lock waits
at most `BUSY_TIMEOUT_MS`. The connection is in autocommit mode (`isolation_level =
None`): transactions are opened only with `BEGIN IMMEDIATE`. It must be used by a single thread.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import sqlite3
import stat
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ponos_coach.activities.ports import StorageBusyError, TransactionStateError
from ponos_coach.fsio import make_subdirectory, replace_file, sync_directory
from ponos_coach.profiles.domain import PROFILE_BACKUP_DIR, PROFILE_DATABASE_NAME
from ponos_coach.profiles.ports import (
    ProfileAlreadyExistsError,
    ProfileCorruptError,
    ProfileFormatTooNewError,
    ProfileNotFoundError,
    ProfileStorageError,
)
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION, V1_TABLES

BUSY_TIMEOUT_MS: Final = 5000
_REPARSE_POINT: Final = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT
_FILE_MODE: Final = 0o600
_CORRUPTION_CODES: Final = (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB)
_PRIMARY_CODE_MASK: Final = 0xFF
_BUSY_CODES: Final = (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
_SIDE_FILE_SUFFIXES: Final = ("-wal", "-shm", "-journal")
_BACKUP_EXTENSION: Final = ".sqlite3"


def is_corruption(exc: sqlite3.Error) -> bool:
    """True if the SQLite error says the file is not an intact database."""
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & _PRIMARY_CODE_MASK) in _CORRUPTION_CODES


class SqliteDatabase:
    """An open, configured connection; shared by all the stores of a profile."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._in_transaction = False

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection (only for the stores of the same profile)."""
        return self._connection

    def user_version(self) -> int:
        """Schema version saved in the file."""
        row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """`BEGIN IMMEDIATE`, commits on normal exit, rolls back on an exception.

        Raises:
            TransactionStateError: a transaction is already open on this connection.
            StorageBusyError: another process holds the database beyond `BUSY_TIMEOUT_MS`; the
                transaction did not begin.
        """
        if self._in_transaction:
            raise TransactionStateError("transaction already open")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            if isinstance(code, int) and (code & _PRIMARY_CODE_MASK) in _BUSY_CODES:
                raise StorageBusyError("database busy") from exc
            raise
        self._in_transaction = True
        try:
            yield
            self._connection.execute("COMMIT")
        except BaseException:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """Inside an open transaction it takes part in it; otherwise it opens one for the block.

        Used by operations of a store that do several writes and take effect all or none
        even outside `transaction()`.
        """
        if self._in_transaction:
            yield
        else:
            with self.transaction():
                yield

    def close(self) -> None:
        """WAL checkpoint and close; the connection closes even if the checkpoint fails.

        Raises:
            sqlite3.Error: the checkpoint did not succeed (the data stay in the WAL, intact).
        """
        try:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._connection.close()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA trusted_schema = OFF")
    return connection


def _configure_durability(connection: sqlite3.Connection) -> None:
    # fetchall: with a single row left in the cursor, a checkpoint right after would give
    # "database table is locked".
    [(mode,)] = connection.execute("PRAGMA journal_mode = WAL").fetchall()
    if str(mode).lower() != "wal":
        raise ProfileStorageError("the file system does not allow WAL mode")
    connection.execute("PRAGMA synchronous = FULL")


def _migrate(db: SqliteDatabase, from_version: int, initial_meta: Mapping[str, str]) -> None:
    """Brings the schema from `from_version` to the latest in a single transaction."""
    connection = db.connection
    with db.transaction():
        for statements in MIGRATIONS[from_version:]:
            for statement in statements:
                connection.execute(statement)
        connection.executemany(
            "INSERT INTO meta (key, value) VALUES (?, ?)", sorted(initial_meta.items())
        )
        connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")


def _remove_files(path: Path) -> None:
    for candidate in (path, *(path.with_name(path.name + s) for s in _SIDE_FILE_SUFFIXES)):
        with contextlib.suppress(OSError):
            candidate.unlink(missing_ok=True)


def create_database(path: Path, initial_meta: Mapping[str, str]) -> SqliteDatabase:
    """Creates `path` (exclusively, permissions for the user only) with the latest schema.

    The `meta` row for each item of `initial_meta` is written in the same transaction as the
    schema. If anything fails, the created file is removed.

    Raises:
        ProfileAlreadyExistsError: the file already exists (even a link); it is not touched.
        ProfileStorageError: creation failed.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.close(os.open(path, flags, _FILE_MODE))
    except FileExistsError as exc:
        raise ProfileAlreadyExistsError("the profile already has a database") from exc
    except OSError as exc:
        raise ProfileStorageError("database creation failed") from exc
    try:
        connection = _connect(path)
        db = SqliteDatabase(connection)
        try:
            _configure_durability(connection)
            _migrate(db, 0, initial_meta)
        except BaseException:
            connection.close()
            raise
    except (sqlite3.Error, StorageBusyError) as exc:
        _remove_files(path)
        raise ProfileStorageError("database creation failed") from exc
    except BaseException:
        _remove_files(path)
        raise
    return db


def _require_regular_file(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as exc:
        raise ProfileNotFoundError("the profile database is missing") from exc
    except OSError as exc:
        raise ProfileStorageError("database not reachable") from exc
    attributes = getattr(status, "st_file_attributes", 0)
    if not stat.S_ISREG(status.st_mode) or attributes & _REPARSE_POINT:
        raise ProfileCorruptError("the database is not a regular file")


def _check_integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if [row[0] for row in rows] != ["ok"]:
        raise ProfileCorruptError("the database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ProfileCorruptError("the database violates foreign keys")


def _require_profile_schema(connection: sqlite3.Connection) -> None:
    """The file has the tables of a profile: a database of another program, even with
    the same `user_version`, does not."""
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    if not {row[0] for row in rows} >= V1_TABLES:
        raise ProfileCorruptError("the file does not contain a profile schema")


def _check_version(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise ProfileFormatTooNewError(f"format {version} newer than the known {SCHEMA_VERSION}")
    if version < 1:
        raise ProfileCorruptError("the file does not contain a profile schema")


def _back_up_before_migration(db: SqliteDatabase, folder: Path, from_version: int) -> None:
    """Consistent copy of the database in `backup/`, before migrating it.

    After the WAL checkpoint, with the SQLite backup API (a consistent snapshot even if
    another process writes), onto an exclusive temporary (0600) then renamed: the name gives the
    starting and target versions. Without the copy it does not migrate.

    Raises:
        ProfileStorageError: copy failed (nothing was migrated).
    """
    backup_dir = folder / PROFILE_BACKUP_DIR
    name = f"{Path(PROFILE_DATABASE_NAME).stem}-v{from_version}-v{SCHEMA_VERSION}"
    final = backup_dir / (name + _BACKUP_EXTENSION)
    temporary = backup_dir / f".{name}-{secrets.token_hex(8)}.tmp"
    try:
        db.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        make_subdirectory(backup_dir)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        os.close(os.open(temporary, flags | getattr(os, "O_NOFOLLOW", 0), _FILE_MODE))
        try:
            with closing(sqlite3.connect(str(temporary))) as target:
                db.connection.backup(target)
            with temporary.open("r+b") as copy:  # fsync needs write access on Windows
                os.fsync(copy.fileno())
            replace_file(temporary, final)
            sync_directory(backup_dir)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise ProfileStorageError("backup copy before migration failed") from exc


def _prepare_existing(db: SqliteDatabase, folder: Path, *, check_integrity: bool) -> None:
    version = db.user_version()
    _check_version(version)
    _require_profile_schema(db.connection)
    if check_integrity or version < SCHEMA_VERSION:  # before migrating it is always checked
        _check_integrity(db.connection)
    if version < SCHEMA_VERSION:
        # Before switching to WAL: a checkpoint right after the mode change gives "table is
        # locked". The file is the previous one, intact, and the backup API copies it consistently.
        _back_up_before_migration(db, folder, version)
    _configure_durability(db.connection)
    if version < SCHEMA_VERSION:
        _migrate(db, version, {})


def open_database(path: Path, *, check_integrity: bool = True) -> SqliteDatabase:
    """Opens a profile's database, bringing it to the latest version if it is older.

    Before migrating it puts a consistent copy in `backup/` (name with the starting and
    target versions). The migration is a single transaction: if it fails the database stays as it
    was.

    `check_integrity=False` skips `quick_check` and `foreign_key_check` (they read the whole file):
    it is only for a second connection to the same profile already verified by the normal
    opening. Version, schema and WAL mode are checked anyway, and a database to
    migrate is always checked.

    Raises:
        ProfileNotFoundError: the file is missing.
        ProfileFormatTooNewError: version newer than the known one; the file is not touched.
        ProfileCorruptError: not a profile's SQLite database, or the integrity check
            fails.
        ProfileStorageError: other failure (lock, I/O, backup copy or migration).
    """
    _require_regular_file(path)
    try:
        connection = _connect(path)
        db = SqliteDatabase(connection)
        try:
            _prepare_existing(db, path.parent, check_integrity=check_integrity)
        except BaseException:
            connection.close()
            raise
    except StorageBusyError as exc:
        raise ProfileStorageError("database busy with another process") from exc
    except sqlite3.Error as exc:
        if is_corruption(exc):
            raise ProfileCorruptError("the file is not an intact database") from exc
        raise ProfileStorageError("database opening failed") from exc
    return db


@dataclass(frozen=True, slots=True)
class DatabasePeek:
    """What is read from a database opened read-only."""

    meta: Mapping[str, str]
    user_version: int
    activity_count: int


def peek_database(path: Path) -> DatabasePeek:
    """Reads `meta`, the version and the number of activities without writing anything or migrating.

    The file is opened read-only (`query_only`). If there is no `-wal` next to it the database is
    closed: it is opened `immutable=1`, which creates no `-wal` or `-shm` (a WAL database opened in
    `mode=ro` leaves them on disk). If the `-wal` is there, the database is open elsewhere (or was
    interrupted): `mode=ro` reads it with its `-shm`, without taking write locks.

    Raises:
        ProfileNotFoundError: the file is missing.
        ProfileFormatTooNewError: version newer than the known one.
        ProfileCorruptError: not a profile's SQLite database.
        ProfileStorageError: other failure (lock, I/O).
    """
    _require_regular_file(path)
    closed = not path.with_name(path.name + "-wal").exists()
    uri = path.resolve().as_uri() + ("?immutable=1" if closed else "?mode=ro")
    try:
        with closing(sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)) as connection:
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            _check_version(version)
            _require_profile_schema(connection)
            meta = dict(connection.execute("SELECT key, value FROM meta ORDER BY key").fetchall())
            count = int(connection.execute("SELECT COUNT(*) FROM activities").fetchone()[0])
    except sqlite3.Error as exc:
        if is_corruption(exc):
            raise ProfileCorruptError("the file is not an intact database") from exc
        raise ProfileStorageError("database reading failed") from exc
    return DatabasePeek(meta, version, count)


def read_meta(db: SqliteDatabase) -> dict[str, str]:
    """All the key-value pairs of `meta`, including those the app does not know."""
    rows: Sequence[tuple[str, str]] = db.connection.execute(
        "SELECT key, value FROM meta ORDER BY key"
    ).fetchall()
    return dict(rows)
