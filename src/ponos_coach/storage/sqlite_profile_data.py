# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the profile's personal data: fixed data and weight series.

Same connection as the other stores: the index transaction covers these too. Only
`?` parameters. Height, weight and date of birth are health data: never in error messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

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
