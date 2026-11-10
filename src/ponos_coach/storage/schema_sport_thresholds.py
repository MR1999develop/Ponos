# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the cycling and other-activities threshold heart rates.

Kept in a module of its own, like the threshold pace's; `sqlite_schema.MIGRATIONS` lists it in its
place. No imports from the project: `sqlite_schema` imports this module.

Two optional columns of `physiology`, so they share the validity date of the minimum, maximum and
running threshold heart rates (one entry per date, one history row, one fingerprint of the load).
NULL means "not set": the default of `threshold_for_sport` applies. When set they obey the same
bounds as the running threshold: at least 40 and `min_bpm < value <= max_bpm`. Existing rows get
NULL, so every existing load is computed as before; the load cache is not cleared because its
fingerprint includes a threshold wherever it differs from the default, so it changes on its own.
"""

from __future__ import annotations

from typing import Final

# Frozen here on purpose: a released migration never changes.
_BIKE = (
    "ALTER TABLE physiology ADD COLUMN bike_threshold_bpm INTEGER CHECK ("
    "bike_threshold_bpm IS NULL OR (bike_threshold_bpm >= 40 "
    "AND bike_threshold_bpm > min_bpm AND bike_threshold_bpm <= max_bpm))"
)
_OTHER = (
    "ALTER TABLE physiology ADD COLUMN other_threshold_bpm INTEGER CHECK ("
    "other_threshold_bpm IS NULL OR (other_threshold_bpm >= 40 "
    "AND other_threshold_bpm > min_bpm AND other_threshold_bpm <= max_bpm))"
)

SPORT_THRESHOLDS_MIGRATION: Final[tuple[str, ...]] = (_BIKE, _OTHER)
