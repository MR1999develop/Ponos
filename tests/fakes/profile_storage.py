# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Database dei profili in memoria: `ProfileStorage`, `OpenProfile` e i tre store.

I dati di ogni profilo vivono nel `InMemoryDisk` (come il file del database): chiudere e
riaprire il profilo li ritrova. Una transazione salva una copia dei dati e la ripristina se
termina con un'eccezione.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityRecord,
    identity_key,
    is_possible_duplicate,
)
from ponos_coach.activities.ports import (
    ActivityIndex,
    DuplicateActivityError,
    TransactionStateError,
)
from ponos_coach.load.domain import ActivityLoad, LoadFingerprint, PhysiologyEntry
from ponos_coach.load.ports import LoadCache, PhysiologyStore, UnknownActivityError
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    FolderKind,
    ProfileId,
    ProfileInfo,
    ProfileName,
)
from ponos_coach.profiles.ports import (
    OpenProfile,
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
)

FORMAT_VERSION = 1


@dataclass
class _Database:
    """Contenuto del database di un profilo."""

    info: ProfileInfo
    records: dict[str, ActivityRecord] = field(default_factory=dict)  # per chiave d'identità
    garmin_ids: set[int] = field(default_factory=set)
    physiology: dict[date, PhysiologyEntry] = field(default_factory=dict)
    loads: dict[str, tuple[LoadFingerprint, ActivityLoad]] = field(default_factory=dict)
    in_transaction: bool = False


@dataclass
class InMemoryDisk:
    """Disco finto condiviso da cartelle e database: nomi contenuti, tipi, lock, database."""

    folders: dict[Path, set[str]] = field(default_factory=dict)
    kinds: dict[Path, FolderKind] = field(default_factory=dict)
    locks: set[Path] = field(default_factory=set)
    databases: dict[Path, _Database] = field(default_factory=dict)

    def kind_of(self, folder: Path) -> FolderKind:
        """Tipo della cartella o del suo antenato più vicino con un tipo dichiarato."""
        for candidate in (folder, *folder.parents):
            if candidate in self.kinds:
                return self.kinds[candidate]
        return FolderKind.LOCAL


class _Index:
    def __init__(self, database: _Database) -> None:
        self._db = database

    @contextmanager
    def transaction(self) -> Iterator[None]:
        db = self._db
        if db.in_transaction:
            raise TransactionStateError("transazione annidata")
        snapshot = copy.deepcopy(
            (db.records, db.garmin_ids, db.physiology, db.loads)
        )  # tipi immutabili: la copia profonda basta
        db.in_transaction = True
        try:
            yield
        except BaseException:
            db.records, db.garmin_ids, db.physiology, db.loads = snapshot
            raise
        finally:
            db.in_transaction = False

    def exists(self, identity: ActivityIdentity) -> bool:
        return identity_key(identity) in self._db.records

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        return self._db.records.get(identity_key(identity))

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        return [
            r
            for r in self.list_by_start()
            if is_possible_duplicate(start, elapsed_s, r.start, r.elapsed_s)
        ]

    def add(self, record: ActivityRecord) -> None:
        key = identity_key(record.identity)
        garmin_id = record.garmin_activity_id
        if key in self._db.records or (garmin_id is not None and garmin_id in self._db.garmin_ids):
            raise DuplicateActivityError(key)
        self._db.records[key] = record
        if garmin_id is not None:
            self._db.garmin_ids.add(garmin_id)

    def list_by_start(self) -> Sequence[ActivityRecord]:
        return sorted(self._db.records.values(), key=lambda r: (r.start, identity_key(r.identity)))


class _Physiology:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def put(self, entry: PhysiologyEntry) -> None:
        self._db.physiology[entry.valid_from] = entry

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        return [self._db.physiology[day] for day in sorted(self._db.physiology)]


class _Cache:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        stored = self._db.loads.get(identity_key(identity))
        return stored[1] if stored is not None and stored[0] == fingerprint else None

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        key = identity_key(identity)
        if key not in self._db.records:
            raise UnknownActivityError(key)
        self._db.loads[key] = (fingerprint, load)

    def invalidate(self, identity: ActivityIdentity) -> None:
        self._db.loads.pop(identity_key(identity), None)

    def clear(self) -> None:
        self._db.loads.clear()


class _OpenProfile:
    def __init__(self, database: _Database) -> None:
        self._db = database
        self._index = _Index(database)
        self._physiology = _Physiology(database)
        self._cache = _Cache(database)

    @property
    def info(self) -> ProfileInfo:
        return self._db.info

    @property
    def activities(self) -> ActivityIndex:
        return self._index

    @property
    def physiology(self) -> PhysiologyStore:
        return self._physiology

    @property
    def load_cache(self) -> LoadCache:
        return self._cache

    def close(self) -> None:
        """Nessun effetto: i dati restano nel disco finto."""


class InMemoryProfileStorage:
    """`ProfileStorage` su un `InMemoryDisk`."""

    def __init__(self, disk: InMemoryDisk | None = None) -> None:
        self.disk = disk or InMemoryDisk()

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """Vedi `ProfileStorage.create`."""
        if folder in self.disk.databases:
            raise ProfileAlreadyExistsError(folder.name)
        info = ProfileInfo(profile_id, name, timezone_name, FORMAT_VERSION)
        database = _Database(info)
        self.disk.databases[folder] = database
        self.disk.folders.setdefault(folder, set()).add(PROFILE_DATABASE_NAME)
        return _OpenProfile(database)

    def open(self, folder: Path) -> OpenProfile:
        """Vedi `ProfileStorage.open`."""
        database = self.disk.databases.get(folder)
        if database is None:
            raise ProfileNotFoundError(folder.name)
        return _OpenProfile(database)
