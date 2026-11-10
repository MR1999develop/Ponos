# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the profile's personal data: fixed data, weight series, sport weights.

Same connection as the other stores: the index transaction covers these too. Only
`?` parameters. Height, weight and date of birth are health data: never in error messages.
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
    ThresholdPace,
    ThresholdPaceEntry,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.ports import FixedDataAlreadySetError, ProfileDataError
from ponos_coach.storage.sqlite_database import SqliteDatabase


class SqliteFixedDataStore:
    """Height and date of birth (`FixedDataStore` port): one row, written only once."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def get(self) -> FixedProfileData | None:
        """The fixed data, or None if the profile does not have them."""
        try:
            row = self._db.connection.execute(
                "SELECT height_cm, birth_date FROM profile_body WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise ProfileDataError("reading the profile data failed") from exc
        if row is None:
            return None
        try:
            return FixedProfileData(Height(row[0]), BirthDate(date.fromisoformat(row[1])))
        except ValueError:  # the text read (a date) is not kept in the chain of causes
            raise ProfileDataError("invalid profile data") from None

    def set_once(self, data: FixedProfileData) -> None:
        """Sets the fixed data; if they are already there, `FixedDataAlreadySetError`."""
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
                raise FixedDataAlreadySetError("height and date of birth are already set") from exc
            raise ProfileDataError("writing the profile data failed") from exc
        except sqlite3.Error as exc:
            raise ProfileDataError("writing the profile data failed") from exc


class SqliteWeightStore:
    """Weight series (`WeightStore` port) on SQLite, in whole tenths of a kilogram."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: WeightEntry) -> None:
        """Saves the weight valid from `entry.valid_from`, replacing the same date."""
        try:
            self._db.connection.execute(
                "INSERT INTO weight (valid_from, tenths_kg) VALUES (?, ?) "
                "ON CONFLICT (valid_from) DO UPDATE SET tenths_kg = excluded.tenths_kg",
                (entry.valid_from.isoformat(), entry.weight.tenths_kg),
            )
        except sqlite3.Error as exc:
            raise ProfileDataError("writing the weight failed") from exc

    def list_entries(self) -> Sequence[WeightEntry]:
        """All the entries by ascending date."""
        try:
            rows = self._db.connection.execute(
                "SELECT valid_from, tenths_kg FROM weight ORDER BY valid_from"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProfileDataError("reading the weight failed") from exc
        try:
            return [WeightEntry(date.fromisoformat(r[0]), Weight(r[1])) for r in rows]
        except ValueError:  # the text read (a date) is not kept in the chain of causes
            raise ProfileDataError("saved weight invalid") from None


class SqliteThresholdPaceStore:
    """Threshold pace series (`ThresholdPaceStore` port) on SQLite, in whole seconds per km."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def put(self, entry: ThresholdPaceEntry) -> None:
        """Saves the pace valid from `entry.valid_from`, replacing the same date."""
        try:
            self._db.connection.execute(
                "INSERT INTO threshold_pace (valid_from, s_per_km) VALUES (?, ?) "
                "ON CONFLICT (valid_from) DO UPDATE SET s_per_km = excluded.s_per_km",
                (entry.valid_from.isoformat(), entry.pace.s_per_km),
            )
        except sqlite3.Error as exc:
            raise ProfileDataError("writing the threshold pace failed") from exc

    def list_entries(self) -> Sequence[ThresholdPaceEntry]:
        """All the entries by ascending date."""
        try:
            rows = self._db.connection.execute(
                "SELECT valid_from, s_per_km FROM threshold_pace ORDER BY valid_from"
            ).fetchall()
        except sqlite3.Error as exc:
            raise ProfileDataError("reading the threshold pace failed") from exc
        try:
            return [ThresholdPaceEntry(date.fromisoformat(r[0]), ThresholdPace(r[1])) for r in rows]
        except ValueError:  # the text read (a date) is not kept in the chain of causes
            raise ProfileDataError("saved threshold pace invalid") from None


class SqliteSportWeightStore:
    """Sport weights chosen by the user (`SportWeightStore` port), non-defaults only."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def load(self) -> Mapping[Sport, SportWeight]:
        """The saved weights."""
        try:
            rows = self._db.connection.execute(
                "SELECT sport, weight FROM sport_weights ORDER BY sport"
            ).fetchall()
        except sqlite3.Error as exc:
            raise LoadStoreError("reading the sport weights failed") from exc
        try:
            return MappingProxyType({Sport(r[0]): SportWeight(r[1]) for r in rows})
        except ValueError as exc:
            raise LoadStoreError("sport weights invalid") from exc

    def replace(self, weights: Mapping[Sport, SportWeight]) -> None:
        """Replaces all the saved weights, all or nothing (even outside a transaction)."""
        try:
            with self._db.atomic():
                self._db.connection.execute("DELETE FROM sport_weights")
                self._db.connection.executemany(
                    "INSERT INTO sport_weights (sport, weight) VALUES (?, ?)",
                    sorted((sport.value, weight.value) for sport, weight in weights.items()),
                )
        except sqlite3.Error as exc:
            raise LoadStoreError("writing the sport weights failed") from exc
