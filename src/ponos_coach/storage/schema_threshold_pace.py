# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema migration for the threshold pace the user sets by hand (a dated series).

Kept in a module of its own, like the workout backup's; `sqlite_schema.MIGRATIONS` lists it in its
place. No imports from the project: `sqlite_schema` imports this module.

`threshold_pace`: one row per validity date, as the weight series; the pace in whole seconds per
kilometre within the plausible range of the threshold tests (1:30 to 60:00). It is a series apart
from the heart-rate parameters: it does not enter the load, so the load cache stays valid.
"""

from __future__ import annotations

from typing import Final

MIN_DATE: Final = "1900-01-01"  # as the weight series
PACE_S_PER_KM: Final = (90, 3600)  # frozen here on purpose: a released migration never changes

_THRESHOLD_PACE = f"""
CREATE TABLE threshold_pace (
    valid_from TEXT    NOT NULL PRIMARY KEY CHECK (date(valid_from) IS valid_from
                                               AND valid_from >= '{MIN_DATE}'),
    s_per_km   INTEGER NOT NULL CHECK (s_per_km BETWEEN {PACE_S_PER_KM[0]} AND {PACE_S_PER_KM[1]})
) STRICT
"""

THRESHOLD_PACE_MIGRATION: Final[tuple[str, ...]] = (_THRESHOLD_PACE,)
