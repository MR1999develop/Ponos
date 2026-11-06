# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ProfileStorage` su SQLite: un file `ponos.sqlite3` nella cartella del profilo."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from pathlib import Path

from ponos_coach.activities.ports import ActivityIndex, SyncJournal
from ponos_coach.load.ports import LoadCache, PhysiologyStore
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
)
from ponos_coach.profiles.ports import OpenProfile, ProfileCorruptError, ProfileStorageError
from ponos_coach.profiles.timezones import require_known_timezone
from ponos_coach.storage.sqlite_database import (
    SqliteDatabase,
    create_database,
    is_corruption,
    open_database,
    peek_database,
    read_meta,
)
from ponos_coach.storage.sqlite_stores import (
    SqliteActivityIndex,
    SqliteLoadCache,
    SqlitePhysiologyStore,
)
from ponos_coach.storage.sqlite_sync import SqliteSyncJournal

_META_ID = "profile_id"
_META_NAME = "name"
_META_TIMEZONE = "timezone"


def _info_from(meta: Mapping[str, str], format_version: int) -> ProfileInfo:
    """Informazioni del profilo da `meta`; mancanti o non valide → `ProfileCorruptError`."""
    try:
        timezone_name = meta[_META_TIMEZONE]
        require_known_timezone(timezone_name)
        return ProfileInfo(
            ProfileId.parse(meta[_META_ID]),
            ProfileName(meta[_META_NAME]),
            timezone_name,
            format_version,
        )
    except (KeyError, ValueError) as exc:
        raise ProfileCorruptError("informazioni del profilo mancanti o non valide") from exc


def _read_info(db: SqliteDatabase) -> ProfileInfo:
    """Informazioni del profilo dal database aperto."""
    try:
        return _info_from(read_meta(db), db.user_version())
    except sqlite3.Error as exc:
        if is_corruption(exc):
            raise ProfileCorruptError("il file non è un database integro") from exc
        raise ProfileStorageError("lettura delle informazioni del profilo non riuscita") from exc


class SqliteOpenProfile:
    """Un profilo aperto: i quattro store condividono la connessione di `SqliteDatabase`."""

    def __init__(self, database: SqliteDatabase, info: ProfileInfo) -> None:
        self._db = database
        self._info = info
        self._activities = SqliteActivityIndex(database)
        self._sync_journal = SqliteSyncJournal(database)
        self._physiology = SqlitePhysiologyStore(database)
        self._load_cache = SqliteLoadCache(database)
        self._closed = False

    @property
    def info(self) -> ProfileInfo:
        """Informazioni lette dal database all'apertura."""
        return self._info

    @property
    def activities(self) -> ActivityIndex:
        """Indice delle attività."""
        return self._activities

    @property
    def sync_journal(self) -> SyncJournal:
        """Giornale della sincronizzazione."""
        return self._sync_journal

    @property
    def physiology(self) -> PhysiologyStore:
        """Parametri fisiologici."""
        return self._physiology

    @property
    def load_cache(self) -> LoadCache:
        """Cache dei carichi."""
        return self._load_cache

    def close(self) -> None:
        """Chiude la connessione (checkpoint compreso); ripeterlo non ha effetti.

        Raises:
            ProfileStorageError: il checkpoint non è riuscito; la connessione è chiusa e i
                dati restano integri nel WAL.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._db.close()
        except sqlite3.Error as exc:
            raise ProfileStorageError("checkpoint alla chiusura non riuscito") from exc


class SqliteProfileStorage:
    """Crea o apre il database `ponos.sqlite3` di un profilo (porta `ProfileStorage`)."""

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """Crea il database con lo schema più recente; non sovrascrive mai un database.

        Raises:
            ProfileAlreadyExistsError: c'è già un database; non si tocca.
            ProfileStorageError: creazione non riuscita; il file creato si toglie.
            ValueError: `timezone_name` sconosciuto (nulla viene scritto).
        """
        require_known_timezone(timezone_name)  # prima di scrivere sul disco
        info = ProfileInfo(profile_id, name, timezone_name, 1)
        db = create_database(
            folder / PROFILE_DATABASE_NAME,
            {
                _META_ID: str(info.profile_id),
                _META_NAME: info.name.value,
                _META_TIMEZONE: info.timezone_name,
            },
        )
        try:
            return SqliteOpenProfile(db, _read_info(db))
        except BaseException:
            db.close()
            raise

    def peek(self, folder: Path) -> ProfilePeek:
        """Informazioni e numero di attività in sola lettura; vedi `ProfileStorage.peek`.

        Raises:
            ProfileNotFoundError: manca il database.
            ProfileFormatTooNewError: versione più nuova di quella nota.
            ProfileCorruptError: database illeggibile o non di un profilo.
            ProfileStorageError: altro guasto.
        """
        peeked = peek_database(folder / PROFILE_DATABASE_NAME)
        return ProfilePeek(_info_from(peeked.meta, peeked.user_version), peeked.activity_count)

    def open(self, folder: Path) -> OpenProfile:
        """Apre il database, migrandolo se è più vecchio (con copia di sicurezza in `backup/`).

        Raises:
            ProfileNotFoundError: manca il database.
            ProfileFormatTooNewError: versione più nuova di quella nota; non si tocca.
            ProfileCorruptError: database illeggibile, non integro o non di un profilo.
            ProfileStorageError: altro guasto o migrazione fallita (il database resta com'era).
        """
        db = open_database(folder / PROFILE_DATABASE_NAME)
        try:
            return SqliteOpenProfile(db, _read_info(db))
        except BaseException:
            db.close()
            raise
