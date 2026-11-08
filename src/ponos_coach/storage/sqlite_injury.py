# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the injuries and illnesses (`InjuryStore` port).

Same connection as the other stores: the index transaction covers it too. Only `?` parameters.
An injury is a row of `injury` with its rows of `injury_pain_free`, `injury_checkin` and
`injury_sign`; replacing it rewrites them all in one atomic step. These are health data: never in
error messages.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from ponos_coach.injury.domain import (
    BodyZone,
    CheckIn,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
    Onset,
    PainFreeActivity,
    PainReport,
    PainScore,
    Problem,
    Questionnaire,
    Side,
    WarningSign,
    problem_id,
)
from ponos_coach.injury.ports import (
    InjuryAlreadyExistsError,
    InjuryNotFoundError,
    InjuryStoreError,
)
from ponos_coach.storage.sqlite_database import SqliteDatabase

_KIND_INJURY = "injury"
_KIND_ILLNESS = "illness"
# Only module constants in the composed queries: no external data (S608).
_COLUMNS = (
    "injury_id, kind, started_on, ended_on, zone, side, onset, onset_day, pain_rest, "
    "pain_walking, pain_running, pain_morning, doctor_notes, reviewed_on, illness_kind"
)
_SELECT = "SELECT " + _COLUMNS + " FROM injury "  # noqa: S608
_INSERT = "INSERT INTO injury (" + _COLUMNS + ") VALUES (" + ", ".join("?" * 15) + ")"  # noqa: S608
_SELECT_ALL = _SELECT + "ORDER BY started_on, injury_id"
_SELECT_ONE = _SELECT + "WHERE injury_id = ?"


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def _main_row(problem: Problem) -> tuple[object, ...]:
    """The values of the `injury` row, in the order of `_COLUMNS`."""
    if isinstance(problem, Illness):
        return (
            problem.illness_id.value,
            _KIND_ILLNESS,
            problem.started_on.isoformat(),
            _iso(problem.recovered_on),
            *(None,) * 10,
            problem.kind.value,
        )
    q = problem.questionnaire
    pain = q.pain
    return (
        problem.injury_id.value,
        _KIND_INJURY,
        problem.opened_on.isoformat(),
        _iso(problem.closed_on),
        q.zone.value,
        q.side.value,
        q.onset.value,
        q.onset_day.isoformat(),
        pain.rest.value,
        pain.walking.value,
        None if pain.running is None else pain.running.value,
        pain.next_morning.value,
        q.doctor_notes,
        _iso(problem.reviewed_on),
        None,
    )


type _Rows = list[tuple[object, ...]]


def _child_rows(injury: Injury) -> tuple[_Rows, _Rows, _Rows]:
    """The rows of the pain-free activities, of the check-ins and of the signs."""
    key = injury.injury_id.value
    free: _Rows = [(key, a.value) for a in injury.questionnaire.pain_free]
    checkins: _Rows = [
        (key, c.day.isoformat(), None if c.pain_during is None else c.pain_during.value,
         c.pain_morning.value)
        for c in injury.checkins
    ]  # fmt: skip
    signs: _Rows = [(key, None, s.value) for s in injury.questionnaire.signs]
    for checkin in injury.checkins:
        signs.extend((key, checkin.day.isoformat(), s.value) for s in checkin.signs)
    return free, checkins, signs


def _write(connection: sqlite3.Connection, problem: Problem) -> None:
    connection.execute(_INSERT, _main_row(problem))
    if isinstance(problem, Illness):
        return
    free, checkins, signs = _child_rows(problem)
    connection.executemany("INSERT INTO injury_pain_free VALUES (?, ?)", free)
    connection.executemany("INSERT INTO injury_checkin VALUES (?, ?, ?, ?)", checkins)
    connection.executemany("INSERT INTO injury_sign VALUES (?, ?, ?)", signs)


def _erase(connection: sqlite3.Connection, key: str) -> int:
    """Deletes the record and its rows (children first); the number of main rows deleted."""
    connection.execute("DELETE FROM injury_sign WHERE injury_id = ?", (key,))
    connection.execute("DELETE FROM injury_checkin WHERE injury_id = ?", (key,))
    connection.execute("DELETE FROM injury_pain_free WHERE injury_id = ?", (key,))
    return connection.execute("DELETE FROM injury WHERE injury_id = ?", (key,)).rowcount


def _pain(value: object) -> PainScore:
    if type(value) is not int:
        raise TypeError("unexpected column types")
    return PainScore(value)


def _optional_pain(value: object) -> PainScore | None:
    return None if value is None else _pain(value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError("unexpected column types")
    return value


def _optional_day(value: object) -> date | None:
    return None if value is None else date.fromisoformat(_text(value))


class _Children:
    """The child rows of every injury read, by injury id."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.free: dict[str, set[PainFreeActivity]] = defaultdict(set)
        self.checkins: dict[str, list[tuple[object, ...]]] = defaultdict(list)
        self.signs: dict[tuple[str, str | None], set[WarningSign]] = defaultdict(set)
        for key, activity in connection.execute("SELECT injury_id, activity FROM injury_pain_free"):
            self.free[key].add(PainFreeActivity(activity))
        for row in connection.execute(
            "SELECT injury_id, day, pain_during, pain_morning FROM injury_checkin ORDER BY day"
        ):
            self.checkins[row[0]].append(row[1:])
        for key, day, sign in connection.execute(
            "SELECT injury_id, checkin_day, sign FROM injury_sign"
        ):
            self.signs[(key, day)].add(WarningSign(sign))


def _checkins_of(key: str, children: _Children) -> tuple[CheckIn, ...]:
    return tuple(
        CheckIn(
            date.fromisoformat(_text(day)),
            _optional_pain(during),
            _pain(morning),
            frozenset(children.signs.get((key, _text(day)), ())),
        )
        for day, during, morning in children.checkins.get(key, ())
    )


def _problem_from(row: Sequence[object], children: _Children) -> Problem:
    """A record from its row and its children; a wrong type or invalid value is an error."""
    key = _text(row[0])
    kind, started = row[1], date.fromisoformat(_text(row[2]))
    ended = _optional_day(row[3])
    if kind == _KIND_ILLNESS:
        return Illness(InjuryId(key), IllnessKind(_text(row[14])), started, ended)
    if kind != _KIND_INJURY:
        raise ValueError("unexpected kind")
    questionnaire = Questionnaire(
        BodyZone(_text(row[4])),
        Side(_text(row[5])),
        Onset(_text(row[6])),
        date.fromisoformat(_text(row[7])),
        PainReport(_pain(row[8]), _pain(row[9]), _optional_pain(row[10]), _pain(row[11])),
        frozenset(children.free.get(key, ())),
        frozenset(children.signs.get((key, None), ())),
        _text(row[12]),
    )
    return Injury(
        InjuryId(key),
        started,
        questionnaire,
        _checkins_of(key, children),
        _optional_day(row[13]),
        ended,
    )


def _decode_rows(
    connection: sqlite3.Connection, rows: Sequence[Sequence[object]]
) -> list[Problem] | None:
    """The records of the rows, or `None` if anything saved is not valid."""
    try:
        children = _Children(connection)
        return [_problem_from(row, children) for row in rows]
    except (ValueError, TypeError, OverflowError):
        return None


class SqliteInjuryStore:
    """The injuries and illnesses (`InjuryStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def add(self, problem: Problem) -> None:
        """Saves a new injury or illness with all its check-ins, all or nothing."""
        try:
            with self._db.atomic():
                _write(self._db.connection, problem)
        except sqlite3.IntegrityError as exc:
            if getattr(exc, "sqlite_errorcode", None) in (
                sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY,
                sqlite3.SQLITE_CONSTRAINT_UNIQUE,
            ):
                raise InjuryAlreadyExistsError("an injury with this id exists") from exc
            raise InjuryStoreError("writing the injury failed") from exc
        except sqlite3.Error as exc:
            raise InjuryStoreError("writing the injury failed") from exc

    def replace(self, problem: Problem) -> None:
        """Replaces the record with the same id, check-ins included, all or nothing."""
        try:
            with self._db.atomic():
                found = _erase(self._db.connection, problem_id(problem).value) > 0
                if found:  # nothing was deleted otherwise: nothing to undo
                    _write(self._db.connection, problem)
        except sqlite3.Error as exc:
            raise InjuryStoreError("writing the injury failed") from exc
        if not found:
            raise InjuryNotFoundError("no injury with this id")

    def remove(self, injury_id: InjuryId) -> None:
        """Deletes the record and its rows."""
        try:
            with self._db.atomic():
                deleted = _erase(self._db.connection, injury_id.value)
        except sqlite3.Error as exc:
            raise InjuryStoreError("deleting the injury failed") from exc
        if deleted == 0:
            raise InjuryNotFoundError("no injury with this id")

    def get(self, injury_id: InjuryId) -> Problem | None:
        """The record with that id, or None."""
        try:
            rows = self._db.connection.execute(_SELECT_ONE, (injury_id.value,)).fetchall()
        except sqlite3.Error as exc:
            raise InjuryStoreError("reading the injury failed") from exc
        found = self._decoded(rows)
        return found[0] if found else None

    def list_problems(self) -> Sequence[Problem]:
        """All the records by ascending start day, then id."""
        try:
            rows = self._db.connection.execute(_SELECT_ALL).fetchall()
        except sqlite3.Error as exc:
            raise InjuryStoreError("reading the injuries failed") from exc
        return self._decoded(rows)

    def _decoded(self, rows: Sequence[Sequence[object]]) -> list[Problem]:
        try:
            decoded = _decode_rows(self._db.connection, rows)
        except sqlite3.Error as exc:
            raise InjuryStoreError("reading the injuries failed") from exc
        if decoded is None:  # outside the handler: the value read is in no cause or context
            raise InjuryStoreError("saved injury invalid")
        return decoded
