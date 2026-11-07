# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite store of the current plan and of the days of the past as they were planned.

Same connection as the other stores. The canonical JSON written by `Plan.to_json()` is saved with
its SHA-256; reading it back is reading input (`plan_codec`): what fails size, checksum or content
is "unreadable", never an exception. Only `?` parameters. Health-related data: never in error
messages.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import date

from ponos_coach.planning.domain import Plan, PlanRefusal, RefusalReason
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.planning.plan_codec import (
    MAX_PLAN_DAY_JSON_CHARS,
    MAX_PLAN_JSON_CHARS,
    PlanDecodingError,
    decode_plan,
    decode_plan_day,
    digest,
)
from ponos_coach.planning.ports import FrozenDay, PlanStoreError, StoredPlan, UnreadablePlan
from ponos_coach.storage.sqlite_database import SqliteDatabase

_KIND_PLAN = "plan"
_KIND_REFUSAL = "refusal"
_ID_SEPARATOR = ","  # goal ids are letters, digits, '_' and '-'


def _stored_plan(row: Sequence[object]) -> StoredPlan | UnreadablePlan:
    """The current state from its row; anything that does not hold together is unreadable."""
    kind, generated_on, version, text, sha256, reason, ids = row
    try:
        if not isinstance(generated_on, str):
            return UnreadablePlan()
        day = date.fromisoformat(generated_on)
        if kind == _KIND_PLAN and isinstance(text, str) and isinstance(sha256, str):
            plan = decode_plan(text, sha256)
            if plan.generated_on != day or plan.method_version != version:
                return UnreadablePlan()
            return StoredPlan(plan, day)
        if kind == _KIND_REFUSAL and isinstance(reason, str) and isinstance(ids, str):
            goal_ids = tuple(ids.split(_ID_SEPARATOR)) if ids else ()
            return StoredPlan(PlanRefusal(RefusalReason(reason), goal_ids), day)
    except (PlanDecodingError, ValueError):
        return UnreadablePlan()
    return UnreadablePlan()


class SqlitePlanStore:
    """The current plan and the frozen past days (`PlanStore` port) on SQLite."""

    def __init__(self, database: SqliteDatabase) -> None:
        self._db = database

    def current(self) -> StoredPlan | UnreadablePlan | None:
        """The current plan or refusal; None if there never was one."""
        try:
            row = self._db.connection.execute(
                "SELECT kind, generated_on, method_version, plan_json, plan_sha256, "
                "refusal_reason, refusal_goal_ids FROM plan_state WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise PlanStoreError("reading the plan failed") from exc
        return None if row is None else _stored_plan(row)

    def replace_current(self, state: Plan | PlanRefusal, generated_on: date) -> None:
        """Makes this the current plan or refusal."""
        if isinstance(state, Plan):
            if state.generated_on != generated_on:
                raise ValueError("the plan was generated on another date")
            text = state.to_json()
            if len(text) > MAX_PLAN_JSON_CHARS:
                raise PlanStoreError("the plan is too big to be saved")
            values: tuple[object, ...] = (
                _KIND_PLAN,
                generated_on.isoformat(),
                state.method_version,
                text,
                digest(text),
                None,
                None,
            )
        else:
            values = (
                _KIND_REFUSAL,
                generated_on.isoformat(),
                None,
                None,
                None,
                state.reason.value,
                _ID_SEPARATOR.join(state.goal_ids),
            )
        try:
            self._db.connection.execute(
                "INSERT INTO plan_state (id, kind, generated_on, method_version, plan_json, "
                "plan_sha256, refusal_reason, refusal_goal_ids) VALUES (1, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, "
                "generated_on = excluded.generated_on, method_version = excluded.method_version, "
                "plan_json = excluded.plan_json, plan_sha256 = excluded.plan_sha256, "
                "refusal_reason = excluded.refusal_reason, "
                "refusal_goal_ids = excluded.refusal_goal_ids",
                values,
            )
        except sqlite3.Error as exc:
            raise PlanStoreError("writing the plan failed") from exc

    def freeze(self, days: Sequence[FrozenDay]) -> None:
        """Keeps these days as they were planned; a day already frozen stays as it is."""
        rows = []
        for frozen in days:
            text = canonical_json(frozen.day)
            if len(text) > MAX_PLAN_DAY_JSON_CHARS:
                raise PlanStoreError("a day of the plan is too big to be saved")
            rows.append(
                (frozen.day.day.isoformat(), frozen.planned_on.isoformat(), text, digest(text))
            )
        try:
            self._db.connection.executemany(
                "INSERT OR IGNORE INTO plan_past_day (day, planned_on, day_json, day_sha256) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
        except sqlite3.Error as exc:
            raise PlanStoreError("writing the past days failed") from exc

    def past_days(self, first: date, last: date) -> Sequence[FrozenDay]:
        """The frozen days from `first` to `last` included, by ascending day."""
        try:
            rows = self._db.connection.execute(
                "SELECT planned_on, day_json, day_sha256 FROM plan_past_day "
                "WHERE day BETWEEN ? AND ? ORDER BY day",
                (first.isoformat(), last.isoformat()),
            ).fetchall()
        except sqlite3.Error as exc:
            raise PlanStoreError("reading the past days failed") from exc
        days = []
        for planned_on, text, sha256 in rows:
            try:
                days.append(
                    FrozenDay(date.fromisoformat(planned_on), decode_plan_day(text, sha256))
                )
            except (PlanDecodingError, ValueError, TypeError):
                continue  # an unreadable day is left out; the history is not rebuildable
        return days
