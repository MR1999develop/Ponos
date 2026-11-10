# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the aerobic threshold (LT1) heart rate of the two sport groups.

Kept in a module of its own, like the load heart rate's; `sqlite_schema.MIGRATIONS` lists it in
its place. No imports from the project: `sqlite_schema` imports this module.

Two optional columns of `physiology`, so they share the validity date of the other heart rates.
NULL means "not entered": the default LT1 (a fixed percentage of the LT2 of the group) applies.
When set they obey `min_bpm < LT1 < LT2`; the LT2 of the other-sports group is
`other_threshold_bpm`, or `threshold_bpm` when that is NULL.

Expand-contract: `bike_threshold_bpm` (migration 15's) stays in the table but is no longer read or
written, because cycling now belongs to the other-sports group; a later migration may drop it.
The load cache is not cleared: its fingerprint includes the load model version, which changed,
so every load is recomputed once on its own.
"""

from __future__ import annotations

from typing import Final

# Frozen here on purpose: a released migration never changes.
_RUN_LT1 = (
    "ALTER TABLE physiology ADD COLUMN run_lt1_bpm INTEGER CHECK ("
    "run_lt1_bpm IS NULL OR (run_lt1_bpm > min_bpm AND run_lt1_bpm < threshold_bpm))"
)
_OTHER_LT1 = (
    "ALTER TABLE physiology ADD COLUMN other_lt1_bpm INTEGER CHECK ("
    "other_lt1_bpm IS NULL OR (other_lt1_bpm > min_bpm "
    "AND other_lt1_bpm < COALESCE(other_threshold_bpm, threshold_bpm)))"
)

LT1_MIGRATION: Final[tuple[str, ...]] = (_RUN_LT1, _OTHER_LT1)
