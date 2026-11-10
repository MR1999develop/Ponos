# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the average and maximum HR of an activity (`load_cache`).

Kept in a module of its own, like the race ascent's; `sqlite_schema.MIGRATIONS` lists it in its
place. No imports from the project.

The two columns are nullable (no HR series, or no sample counted). The cache is derived: it is
emptied so that every load is recomputed once from the files (the load model version also
changes), and the old rows, which would have NULL, are never read as "no heart rate".
"""

from __future__ import annotations

from typing import Final

BPM: Final = (20, 250)  # frozen: as the limits of `physiology`

_CLEAR = "DELETE FROM load_cache"
_AVG = (
    "ALTER TABLE load_cache ADD COLUMN avg_bpm INTEGER "
    f"CHECK (avg_bpm IS NULL OR avg_bpm BETWEEN {BPM[0]} AND {BPM[1]})"
)
_MAX = (
    "ALTER TABLE load_cache ADD COLUMN max_bpm INTEGER "
    f"CHECK (max_bpm IS NULL OR max_bpm BETWEEN {BPM[0]} AND {BPM[1]})"
)

LOAD_HEART_RATE_MIGRATION: Final[tuple[str, ...]] = (_CLEAR, _AVG, _MAX)
