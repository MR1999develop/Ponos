# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Connessione SQLite di un profilo: creazione, apertura, migrazioni e transazioni.

Scelte di durabilità: `foreign_keys = ON` e `trusted_schema = OFF` a ogni connessione;
`journal_mode = WAL` con `synchronous = FULL` (la scrittura nel WAL si svuota su disco a ogni
commit, quindi un'attività confermata sopravvive a un'interruzione di corrente); attesa dei
lock al più `BUSY_TIMEOUT_MS`. La connessione è in modalità autocommit (`isolation_level =
None`): le transazioni si aprono solo con `BEGIN IMMEDIATE`. Va usata da un solo thread.
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

from ponos_coach.activities.ports import TransactionStateError
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
_SIDE_FILE_SUFFIXES: Final = ("-wal", "-shm", "-journal")
_BACKUP_EXTENSION: Final = ".sqlite3"


def is_corruption(exc: sqlite3.Error) -> bool:
    """Vero se l'errore di SQLite dice che il file non è un database integro."""
    code = getattr(exc, "sqlite_errorcode", None)
    return isinstance(code, int) and (code & _PRIMARY_CODE_MASK) in _CORRUPTION_CODES


class SqliteDatabase:
    """Una connessione aperta e configurata; la condividono tutti gli store di un profilo."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._in_transaction = False

    @property
    def connection(self) -> sqlite3.Connection:
        """La connessione sottostante (solo per gli store dello stesso profilo)."""
        return self._connection

    def user_version(self) -> int:
        """Versione dello schema salvata nel file."""
        row = self._connection.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """`BEGIN IMMEDIATE`, conferma all'uscita normale, annulla con un'eccezione.

        Raises:
            TransactionStateError: una transazione è già aperta su questa connessione.
        """
        if self._in_transaction:
            raise TransactionStateError("transazione già aperta")
        self._connection.execute("BEGIN IMMEDIATE")
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
        """Dentro una transazione aperta ne fa parte; altrimenti ne apre una per il blocco.

        Serve alle operazioni di uno store che fanno più scritture e valgono tutte o nessuna
        anche fuori da `transaction()`.
        """
        if self._in_transaction:
            yield
        else:
            with self.transaction():
                yield

    def close(self) -> None:
        """Checkpoint del WAL e chiusura; la connessione si chiude anche se il checkpoint fallisce.

        Raises:
            sqlite3.Error: il checkpoint non è riuscito (i dati restano nel WAL, integri).
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
    # fetchall: con una riga sola lasciata nel cursore, un checkpoint subito dopo darebbe
    # "database table is locked".
    [(mode,)] = connection.execute("PRAGMA journal_mode = WAL").fetchall()
    if str(mode).lower() != "wal":
        raise ProfileStorageError("il file system non permette la modalità WAL")
    connection.execute("PRAGMA synchronous = FULL")


def _migrate(db: SqliteDatabase, from_version: int, initial_meta: Mapping[str, str]) -> None:
    """Porta lo schema da `from_version` all'ultimo in una sola transazione."""
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
    """Crea `path` (in modo esclusivo, permessi solo per l'utente) con lo schema più recente.

    La riga di `meta` per ogni voce di `initial_meta` si scrive nella stessa transazione dello
    schema. Se qualcosa fallisce, il file creato si toglie.

    Raises:
        ProfileAlreadyExistsError: il file esiste già (anche un collegamento); non si tocca.
        ProfileStorageError: creazione non riuscita.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        os.close(os.open(path, flags, _FILE_MODE))
    except FileExistsError as exc:
        raise ProfileAlreadyExistsError("il profilo ha già un database") from exc
    except OSError as exc:
        raise ProfileStorageError("creazione del database non riuscita") from exc
    try:
        connection = _connect(path)
        db = SqliteDatabase(connection)
        try:
            _configure_durability(connection)
            _migrate(db, 0, initial_meta)
        except BaseException:
            connection.close()
            raise
    except sqlite3.Error as exc:
        _remove_files(path)
        raise ProfileStorageError("creazione del database non riuscita") from exc
    except BaseException:
        _remove_files(path)
        raise
    return db


def _require_regular_file(path: Path) -> None:
    try:
        status = path.lstat()
    except FileNotFoundError as exc:
        raise ProfileNotFoundError("manca il database del profilo") from exc
    except OSError as exc:
        raise ProfileStorageError("database non raggiungibile") from exc
    attributes = getattr(status, "st_file_attributes", 0)
    if not stat.S_ISREG(status.st_mode) or attributes & _REPARSE_POINT:
        raise ProfileCorruptError("il database non è un file regolare")


def _check_integrity(connection: sqlite3.Connection) -> None:
    rows = connection.execute("PRAGMA quick_check").fetchall()
    if [row[0] for row in rows] != ["ok"]:
        raise ProfileCorruptError("il controllo di integrità del database è fallito")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ProfileCorruptError("il database viola le chiavi esterne")


def _require_profile_schema(connection: sqlite3.Connection) -> None:
    """Il file ha le tabelle di un profilo: un database di un altro programma, anche con
    `user_version` uguale, non lo è."""
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    if not {row[0] for row in rows} >= V1_TABLES:
        raise ProfileCorruptError("il file non contiene lo schema di un profilo")


def _check_version(version: int) -> None:
    if version > SCHEMA_VERSION:
        raise ProfileFormatTooNewError(f"formato {version} più nuovo del {SCHEMA_VERSION} noto")
    if version < 1:
        raise ProfileCorruptError("il file non contiene lo schema di un profilo")


def _back_up_before_migration(db: SqliteDatabase, folder: Path, from_version: int) -> None:
    """Copia coerente del database in `backup/`, prima di migrarlo.

    Dopo il checkpoint del WAL, con l'API di backup di SQLite (un'istantanea coerente anche se
    un altro processo scrive), su un temporaneo esclusivo (0600) poi rinominato: il nome dice le
    versioni di partenza e di arrivo. Senza la copia non si migra.

    Raises:
        ProfileStorageError: copia non riuscita (nulla è stato migrato).
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
            with temporary.open("r+b") as copy:  # fsync vuole l'accesso in scrittura su Windows
                os.fsync(copy.fileno())
            replace_file(temporary, final)
            sync_directory(backup_dir)
        except BaseException:
            with contextlib.suppress(OSError):
                temporary.unlink()
            raise
    except (OSError, sqlite3.Error) as exc:
        raise ProfileStorageError("copia di sicurezza prima della migrazione non riuscita") from exc


def _prepare_existing(db: SqliteDatabase, folder: Path) -> None:
    version = db.user_version()
    _check_version(version)
    _require_profile_schema(db.connection)
    _check_integrity(db.connection)
    if version < SCHEMA_VERSION:
        # Prima di passare a WAL: un checkpoint subito dopo il cambio di modalità dà "table is
        # locked". Il file è quello di prima, intatto, e l'API di backup lo copia coerente.
        _back_up_before_migration(db, folder, version)
    _configure_durability(db.connection)
    if version < SCHEMA_VERSION:
        _migrate(db, version, {})


def open_database(path: Path) -> SqliteDatabase:
    """Apre il database di un profilo, portandolo alla versione più recente se è più vecchio.

    Prima di migrare mette in `backup/` una copia coerente (nome con le versioni di partenza e
    di arrivo). La migrazione è una sola transazione: se fallisce il database resta com'era.

    Raises:
        ProfileNotFoundError: il file manca.
        ProfileFormatTooNewError: versione più nuova di quella nota; il file non si tocca.
        ProfileCorruptError: non è un database SQLite di un profilo, o il controllo di integrità
            fallisce.
        ProfileStorageError: altro guasto (lock, I/O, copia di sicurezza o migrazione).
    """
    _require_regular_file(path)
    try:
        connection = _connect(path)
        db = SqliteDatabase(connection)
        try:
            _prepare_existing(db, path.parent)
        except BaseException:
            connection.close()
            raise
    except sqlite3.Error as exc:
        if is_corruption(exc):
            raise ProfileCorruptError("il file non è un database integro") from exc
        raise ProfileStorageError("apertura del database non riuscita") from exc
    return db


@dataclass(frozen=True, slots=True)
class DatabasePeek:
    """Ciò che si legge da un database aperto in sola lettura."""

    meta: Mapping[str, str]
    user_version: int
    activity_count: int


def peek_database(path: Path) -> DatabasePeek:
    """Legge `meta`, la versione e il numero di attività senza scrivere nulla né migrare.

    Il file si apre solo in lettura (`query_only`). Se accanto non c'è un `-wal` il database è
    chiuso: si apre `immutable=1`, che non crea `-wal` né `-shm` (un database WAL aperto in
    `mode=ro` li lascia sul disco). Se il `-wal` c'è, il database è aperto altrove (o si è
    interrotto): `mode=ro` lo legge con il suo `-shm`, senza prendere lock di scrittura.

    Raises:
        ProfileNotFoundError: il file manca.
        ProfileFormatTooNewError: versione più nuova di quella nota.
        ProfileCorruptError: non è un database SQLite di un profilo.
        ProfileStorageError: altro guasto (lock, I/O).
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
            raise ProfileCorruptError("il file non è un database integro") from exc
        raise ProfileStorageError("lettura del database non riuscita") from exc
    return DatabasePeek(meta, version, count)


def read_meta(db: SqliteDatabase) -> dict[str, str]:
    """Tutte le coppie chiave-valore di `meta`, comprese quelle che l'app non conosce."""
    rows: Sequence[tuple[str, str]] = db.connection.execute(
        "SELECT key, value FROM meta ORDER BY key"
    ).fetchall()
    return dict(rows)
