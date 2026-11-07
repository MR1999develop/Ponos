# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Every number of the plan engine, in one place.

All of them are project defaults (the project skill `ponos-allenamento`, `references/piano.md`,
says why), unless a source is named: Friel for the zones and the 80/20 split, Riegel for the
race-time prediction. Changing a value changes the plan of every user: raise `METHOD_VERSION`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ponos_coach.goals.domain import SessionType
from ponos_coach.planning.domain.phases import Phase

# Saved with every plan; changes when the way a plan is computed changes.
METHOD_VERSION: Final = 1

# --- Horizon -----------------------------------------------------------------------------------
HORIZON_DAYS: Final = 364  # a plan covers 52 weeks from tomorrow at most

# --- Macrocycle, backwards from each A race ----------------------------------------------------
SHORT_RACE_MAX_M: Final = 10_000  # up to a 10 km
HALF_MARATHON_M: Final = 21_097  # up to the half marathon
TAPER_DAYS_SHORT: Final = 7
TAPER_DAYS_HALF: Final = 10
TAPER_DAYS_LONG: Final = 14  # marathon and beyond
RECOVERY_DAYS_SHORT: Final = 3  # easy days after an A race
RECOVERY_DAYS_HALF: Final = 5
RECOVERY_DAYS_LONG: Final = 7
PEAK_DAYS: Final = 14
BUILD_MAX_DAYS: Final = 8 * 7
BASE_MAX_DAYS: Final = 12 * 7
# A block of peak, build and base needs at least a peak's worth of free days; with fewer the
# days are maintenance (two A races three weeks apart: only recovery and maintenance between).
MIN_BLOCK_DAYS: Final = PEAK_DAYS
MINI_TAPER_DAYS_B: Final = 4  # B race: days of mini unload

# --- CTL target and ramp -----------------------------------------------------------------------
# CTL to reach for an A race by distance in metres; linear in between, flat outside.
CTL_BY_DISTANCE: Final = (
    (5_000, 40.0),
    (10_000, 45.0),
    (21_097, 55.0),
    (42_195, 70.0),
    (50_000, 80.0),
)
MAX_RAMP_PER_WEEK: Final = 5.0  # CTL points per week
MAX_RAMP_PER_WEEK_AFTER_INJURY: Final = 3.0
# Share of the weeks before a target that count as ramp weeks: the unload weeks and the weeks the
# user's time cannot fill do not gain anything, so the ramp is planned a little steeper.
LOADED_WEEK_SHARE: Final = 0.6
CTL_TOLERANCE: Final = 5.0  # missing a target by less than a week of ramp is not worth a warning
# Daily load of a normal day at the least, so that a CTL of 0 still gets some training.
MIN_DAILY_LOAD: Final = 10.0

# --- Mesocycle ---------------------------------------------------------------------------------
LOADED_WEEKS_PER_MESOCYCLE: Final = 3  # 3:1
UNLOAD_FRACTION: Final = 0.65  # unload week and taper days, in the 60-70% range
MIN_LOADED_DAYS_IN_WEEK: Final = 4  # a week with fewer is not counted in the 3:1 cycle

# --- Week --------------------------------------------------------------------------------------
MAX_INTENSE_PER_WEEK: Final = 3
MAX_INTENSE_PER_WEEK_BASE: Final = 2
LOW_ZONE_SHARE_PERCENT: Final = 80  # 80/20 by time: at least this much in Z1-Z2 (base, maintenance)
ROUND_MINUTES: Final = 5  # sessions last a multiple of this
TAPER_EVE_MAX_MINUTES: Final = 30  # the session of the day before a race
RECOVERY_SESSION_MINUTES: Final = 30
SCALE_ATTEMPTS: Final = 24  # tries to fit a week in the ramp and hours limits

# Intense work in a week, in the order it is scheduled (and dropped in reverse for the 80/20).
WEEK_QUALITY: Final[dict[Phase, tuple[SessionType, ...]]] = {
    Phase.MAINTENANCE: (SessionType.THRESHOLD,),
    Phase.BASE: (SessionType.THRESHOLD, SessionType.MEDIUM),
    Phase.BUILD: (SessionType.THRESHOLD, SessionType.VO2MAX),
    Phase.PEAK: (SessionType.THRESHOLD, SessionType.VO2MAX),
}
# Relative share of a group's load budget that a session type takes.
SESSION_SHARE: Final[dict[SessionType, float]] = {
    SessionType.LONG: 2.0,
    SessionType.EASY: 1.0,
    SessionType.MEDIUM: 1.0,
    SessionType.THRESHOLD: 1.0,
    SessionType.VO2MAX: 0.8,
    SessionType.RECOVERY: 0.5,
    SessionType.STRENGTH: 0.5,
    SessionType.ALTERNATIVE: 1.0,
}
# When the budget cannot pay every session's minimum, the lowest priority goes first.
KEEP_PRIORITY: Final[dict[SessionType, int]] = {
    SessionType.LONG: 6,
    SessionType.THRESHOLD: 5,
    SessionType.VO2MAX: 4,
    SessionType.MEDIUM: 3,
    SessionType.EASY: 2,
    SessionType.ALTERNATIVE: 2,
    SessionType.STRENGTH: 1,
    SessionType.RECOVERY: 1,
}
# Shortest and longest session of a (continuous) type, in minutes.
MIN_MINUTES: Final[dict[SessionType, int]] = {
    SessionType.EASY: 20,
    SessionType.LONG: 60,
    SessionType.MEDIUM: 40,
    SessionType.RECOVERY: 20,
    SessionType.STRENGTH: 30,
    SessionType.ALTERNATIVE: 30,
}
MAX_MINUTES: Final[dict[SessionType, int]] = {
    SessionType.EASY: 90,
    SessionType.LONG: 240,
    SessionType.MEDIUM: 90,
    SessionType.RECOVERY: 40,
    SessionType.STRENGTH: 60,
    SessionType.ALTERNATIVE: 120,
}

# --- Session models ----------------------------------------------------------------------------
# Continuous sessions: (role, share of the duration in percent, zone); the shares add up to 100.
CONTINUOUS_MODELS: Final[dict[SessionType, tuple[tuple[str, int, int], ...]]] = {
    SessionType.EASY: (("warmup", 10, 1), ("steady", 90, 2)),
    SessionType.RECOVERY: (("steady", 100, 1),),
    SessionType.LONG: (("steady", 85, 2), ("steady", 15, 3)),
    SessionType.MEDIUM: (("warmup", 20, 2), ("steady", 60, 3), ("cooldown", 20, 1)),
    SessionType.STRENGTH: (("steady", 100, 2),),
    SessionType.ALTERNATIVE: (("steady", 100, 2),),
}


@dataclass(frozen=True, slots=True)
class IntervalModel:
    """Interval session: warm-up (Z2), `reps` x work (zone `zone`) with rests (Z1) between,
    cool-down (Z1). All in minutes; the rest follows every repetition but the last."""

    warmup: int
    cooldown: int
    work: int
    rest: int
    zone: int
    min_reps: int
    max_reps: int

    def total_minutes(self, reps: int) -> int:
        """Duration with `reps` repetitions."""
        return self.warmup + self.cooldown + reps * self.work + (reps - 1) * self.rest


INTERVAL_MODELS: Final[dict[tuple[SessionType, Phase], IntervalModel]] = {
    (SessionType.THRESHOLD, Phase.MAINTENANCE): IntervalModel(15, 10, 8, 2, 4, 2, 3),
    (SessionType.THRESHOLD, Phase.BASE): IntervalModel(15, 10, 8, 2, 4, 2, 4),
    (SessionType.THRESHOLD, Phase.BUILD): IntervalModel(15, 10, 10, 3, 4, 2, 4),
    (SessionType.THRESHOLD, Phase.PEAK): IntervalModel(15, 10, 12, 3, 4, 2, 4),
    (SessionType.VO2MAX, Phase.BUILD): IntervalModel(15, 10, 3, 3, 5, 4, 6),
    (SessionType.VO2MAX, Phase.PEAK): IntervalModel(15, 10, 4, 3, 5, 4, 6),
}

# Heart rate that stands for a zone when estimating a session's load, in percent of the threshold.
ZONE_REPRESENTATIVE_PERCENT: Final = (75, 87, 92, 97, 103)

# Race effort: zone shares by expected duration, (up to minutes, shares of Z1..Z5).
RACE_ZONE_MIX: Final = (
    (60, (0.0, 0.0, 0.2, 0.6, 0.2)),
    (150, (0.0, 0.0, 0.3, 0.7, 0.0)),
    (10**9, (0.0, 0.4, 0.6, 0.0, 0.0)),
)
DEFAULT_RACE_PACE_S_PER_KM: Final = 360  # to size a race's load when nothing else is known

# --- Adaptation --------------------------------------------------------------------------------
RECOVERY_TSB: Final = -30.0  # tomorrow's TSB below this inserts recovery days
RECOVERY_ATL_CTL_RATIO: Final = 1.5
RATIO_MIN_CTL: Final = 10.0  # the ATL/CTL ratio means little below this CTL
RECOVERY_DAYS_ADAPTATION: Final = 2

# --- Target time -------------------------------------------------------------------------------
RIEGEL_EXPONENT: Final = 1.06
IMPROVEMENT_PERMILLE_PER_WEEK: Final = 5  # 0.5% a week
MAX_IMPROVEMENT_PERMILLE: Final = 80  # 8%
AMBITIOUS_MARGIN_PERCENT: Final = 3  # ambitious: within a further 3% of the best time
THRESHOLD_HOUR_S: Final = 3600  # the threshold pace is the pace sustainable for an hour

PARAMETER_NAMES: Final = (
    "horizon_days",
    "taper_days_short",
    "taper_days_half",
    "taper_days_long",
    "peak_days",
    "build_max_days",
    "base_max_days",
    "mini_taper_days_b",
    "max_ramp_per_week",
    "unload_fraction",
    "loaded_weeks_per_mesocycle",
    "max_intense_per_week",
    "max_intense_per_week_base",
    "low_zone_share_percent",
    "recovery_tsb",
    "recovery_atl_ctl_ratio",
    "recovery_days_adaptation",
    "riegel_exponent",
    "ambitious_margin_percent",
)


def parameter_snapshot(max_ramp: float) -> tuple[tuple[str, float], ...]:
    """The parameters a plan was generated with, in a fixed order (saved with the plan)."""
    values = (
        HORIZON_DAYS,
        TAPER_DAYS_SHORT,
        TAPER_DAYS_HALF,
        TAPER_DAYS_LONG,
        PEAK_DAYS,
        BUILD_MAX_DAYS,
        BASE_MAX_DAYS,
        MINI_TAPER_DAYS_B,
        max_ramp,
        UNLOAD_FRACTION,
        LOADED_WEEKS_PER_MESOCYCLE,
        MAX_INTENSE_PER_WEEK,
        MAX_INTENSE_PER_WEEK_BASE,
        LOW_ZONE_SHARE_PERCENT,
        RECOVERY_TSB,
        RECOVERY_ATL_CTL_RATIO,
        RECOVERY_DAYS_ADAPTATION,
        RIEGEL_EXPONENT,
        AMBITIOUS_MARGIN_PERCENT,
    )
    return tuple((name, float(value)) for name, value in zip(PARAMETER_NAMES, values, strict=True))
