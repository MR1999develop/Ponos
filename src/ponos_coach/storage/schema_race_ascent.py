# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the elevation gain of a race (`goal.ascent_m`).

Kept in a module of its own, like the threshold pace's; `sqlite_schema.MIGRATIONS` lists it in its
place. No imports from the project: `sqlite_schema` imports this module.

The column is the positive elevation gain in whole metres, 0 for a flat race: the rows that exist
have 0, so a race saved before keeps meaning what it meant. A fitness goal has 0 too (the domain
writes it so). The limits are frozen here on purpose: a released migration never changes.
"""

from __future__ import annotations

from typing import Final

ASCENT_M: Final = (0, 30_000)  # as `goals.domain.goal.MAX_ASCENT_M`, frozen

_GOAL_ASCENT = (
    "ALTER TABLE goal ADD COLUMN ascent_m INTEGER NOT NULL DEFAULT 0 "
    f"CHECK (ascent_m BETWEEN {ASCENT_M[0]} AND {ASCENT_M[1]})"
)

RACE_ASCENT_MIGRATION: Final[tuple[str, ...]] = (_GOAL_ASCENT,)
