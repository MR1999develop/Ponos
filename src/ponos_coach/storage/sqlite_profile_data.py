# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Store SQLite dei dati personali del profilo: dati fissi, serie del peso, pesi degli sport.

Stessa connessione degli altri store: la transazione dell'indice copre anche questi. Solo
parametri `?`. Altezza, peso e data di nascita sono dati di salute: mai nei messaggi d'errore.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date
from types import MappingProxyType

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.load.ports import LoadStoreError
from ponos_coach.profiles.domain import (
    BirthDate,
    FixedProfileData,
    Height,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import FixedDataAlreadySetError, ProfileDataError
from ponos_coach.storage.sqlite_database import SqliteDatabase


class SqliteFixedDataStore:
    """Altezza e data di nascita (porta `FixedDataStore`): una riga, scritta una volta sola."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self) -> FixedProfileData | None:
        """I dati fissi, o None se il profilo non li ha."""
        try:
            row = self._db.connection.execute(
                "SELECT height_cm, birth_date FROM profile_body WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise ProfileDataError("lettura dei dati del profilo non riuscita") from exc
        if row is None:
            return None
        try:
            return FixedProfileData(Height(row[0]), BirthDate(date.fromisoformat(row[1])))
        except ValueError as exc:
            raise ProfileDataError("dati del profilo non validi") from exc

    def set_once(self, data: FixedProfileData) -> None:
        """Imposta i dati fissi; se ci sono già, `FixedDataAlreadySetError`."""
        try:
            self._db.connection.execute(
                "INSERT INTO profile_body (id, height_cm, birth_date) VALUES (1, ?, ?)",
                (data.height.cm, data.birth_date.value.isoformat()),
            )
        except sqlite3.IntegrityError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise FixedDataAlreadySetError(
                    "altezza e data di nascita sono già impostate"
                ) from exc
            raise ProfileDataError("scrittura dei dati del profilo non riuscita") from exc
        except sqlite3.Error as exc:
            raise ProfileDataError("scrittura dei dati del profilo non riuscita") from exc


class SqliteWeightStore:
    """Serie del peso (porta `WeightStore`) su SQLite, in decimi di chilogrammo interi."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: WeightEntry) -> None:
        """Salva il peso valido da `entry.valid_from`, sostituendo la stessa data."""
        try:
            self._db.connection.execute(
                "INSERT INTO weight (valid_from, tenths_kg) VALUES (?, ?) "
                "ON CONFLICT (valid_from) DO UPDATE SET tenths_kg = excluded.tenths_kg",
                (entry.valid_from.isoformat(), entry.weight.tenths_kg),
            )
        except sqlite3.Error as exc:
            raise ProfileDataError("scrittura del peso non riuscita") from exc

    def list_entries(self) -> Sequence[WeightEntry]:
        """Tutte le voci per data crescente."""
        try:
            rows = self._db.connection.execute(
                "SELECT valid_from, tenths_kg FROM weight ORDER BY valid_from"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProfileDataError("lettura del peso non riuscita") from exc
        try:
            return [WeightEntry(date.fromisoformat(r[0]), Weight(r[1])) for r in rows]
        except ValueError as exc:
            raise ProfileDataError("peso salvato non valido") from exc


class SqliteSportWeightStore:
    """Pesi degli sport scelti dall'utente (porta `SportWeightStore`), solo i non-default."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def load(self) -> Mapping[Sport, SportWeight]:
        """I pesi salvati."""
        try:
            rows = self._db.connection.execute(
                "SELECT sport, weight FROM sport_weights ORDER BY sport"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LoadStoreError("lettura dei pesi degli sport non riuscita") from exc
        try:
            return MappingProxyType({Sport(r[0]): SportWeight(r[1]) for r in rows})
        except ValueError as exc:
            raise LoadStoreError("pesi degli sport non validi") from exc

    def replace(self, weights: Mapping[Sport, SportWeight]) -> None:
        """Sostituisce tutti i pesi salvati, tutto o niente (anche fuori da una transazione)."""
        try:
            with self._db.atomic():
                self._db.connection.execute("DELETE FROM sport_weights")
                self._db.connection.executemany(
                    "INSERT INTO sport_weights (sport, weight) VALUES (?, ?)",
                    sorted((sport.value, weight.value) for sport, weight in weights.items()),
                )
        except sqlite3.Error as exc:
            raise LoadStoreError("scrittura dei pesi degli sport non riuscita") from exc
