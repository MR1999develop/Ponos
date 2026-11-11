# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phases of the macrocycle, one per day."""

from __future__ import annotations

from enum import Enum


class Phase(Enum):
    """Phase of a plan day. The value is a stable identifier (used in saved plans)."""

    MAINTENANCE = "maintenance"
    BASE = "base"
    BUILD = "build"
    PEAK = "peak"
    RECOVERY = "recovery"  # after a race, or inserted by the adaptation
    MINI_TAPER = "mini_taper"  # the days before a B race
    TAPER = "taper"  # the unload before an A race
    RACE = "race"


# When two phases claim a day the higher rank wins (a race day is always a race day).
PHASE_RANK: dict[Phase, int] = {
    Phase.MAINTENANCE: 0,
    Phase.BASE: 1,
    Phase.BUILD: 2,
    Phase.PEAK: 3,
    Phase.RECOVERY: 4,
    Phase.MINI_TAPER: 5,
    Phase.TAPER: 6,
    Phase.RACE: 7,
}

# Days that carry the normal training load (the others are unloaded or are a race).
LOADED_PHASES = frozenset({Phase.MAINTENANCE, Phase.BASE, Phase.BUILD, Phase.PEAK})
TAPER_PHASES = frozenset({Phase.TAPER, Phase.MINI_TAPER})


class RaceClass(Enum):
    """Length class of a race, which decides its taper and its race-pace work."""

    SHORT = "short"  # up to a 10 km
    HALF = "half"  # up to the half marathon
    LONG = "long"  # the marathon and beyond
