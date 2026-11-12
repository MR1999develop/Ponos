# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Every number of the plan engine, in one place.

All of them are project defaults (the project skill `ponos-allenamento`, `references/piano.md`,
says why), unless a source is named (the codes are those of the skill's table of sources): Friel
for the zones, Riegel for the race-time prediction, TON24 / HAU22 / SAN25 / KNO24 / TON14 / MUN15
for the weekly distribution, the stepped taper and the race-pace work. Changing a value changes
the plan of every user: raise `METHOD_VERSION`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ponos_coach.goals.domain import SessionType
from ponos_coach.planning.domain.phases import Phase, RaceClass

# Saved with every plan; changes when the way a plan is computed changes.
# 3: heart-rate zones of the 5th edition (Z2 from 78%, Z3 from 87%, Z4 from 94% of the threshold).
# 4: open injuries and illnesses (`PlanInputs.injuries`): the plan stops, or drops the sessions
# that load the injured zone, and the weekly ramp is the lower one while they last.
# 5: the threshold test is a session of the plan (`threshold_test` on the planned session,
# the protocol and the missed tests among the inputs): ADR 015.
# 6: the user chooses the days and how many sessions a week, not the minutes of each day: the
# sessions go on `sessions_per_week` of the available days (`WeeklyAvailability.session_days`)
# and a day is as long as the types allow; the long session is the longest of its week.
# 7: the load is TRIMPeq (no hrTSS, no sport weights): the session rates of a sport carry no weight;
# the scale is still 100 = one hour at the threshold, so the running plan is the same as before.
# 8: the elevation gain of a race (`RaceGoal.ascent_m`): the time and the pace of a race are
# computed on the flat-equivalent distance, and a steep A race gets hill repeats and hilly long
# runs in the build and the peak.
# 9: a `CtlTargetNotReached` says whether the weekly hours stop it (`LimitedByHours`, with the
# minutes that would be enough) or the ramp and the time (`LimitedByRamp`); the plan itself is
# the same, but the saved plans lack the cause, so they are rebuilt.
# 10: heart-rate zones chosen by the user on 2026-11-10 (Z2 from 75%, Z3 from 85%, Z4 from 95%,
# Z5 from 103% of the threshold): the HR ranges of the blocks and the estimated loads change.
# 11: the Z1/Z2 boundary moves from 75% to 72% of the threshold (user's request, 2026-11-11): the
# lower bound of the Z2 blocks and the upper bound of the Z1 blocks change.
# 12: cycling races (user's request, 2026-11-11): no target time; their time is estimated from
# distance and elevation gain at a cycling speed, and the rules that go by the distance of a race
# (class, taper, recovery, CTL to reach, long session) use the running distance of equal duration.
# 13: a missed session is made up (user's request, 2026-11-12): the long and the intense sessions
# planned earlier in the current week and not done (`PlanInputs.missed_sessions`) go on the days
# left in that week, within the usual rules; the session types fall back to the suggested ones
# for a sport with none chosen. Mixed sports: up to a race of another sport than the plan's, every
# second easy session of a week is in that sport, and in its last `OTHER_SPORT_LONG_DAYS` days
# the long session too (user's request, 2026-11-12: bike and running sessions mixed).
METHOD_VERSION: Final = 13

# --- Why a CTL target is missed ----------------------------------------------------------------
# To tell hours from ramp the engine rebuilds the plan with a higher weekly cap, on a grid of
# multiples of the step, up to the largest cap it tries (20 hours); beyond it the limit is not
# the hours.
HOURS_SEARCH_STEP_MIN: Final = 30
HOURS_SEARCH_MAX_MIN: Final = 1200

# --- Horizon -----------------------------------------------------------------------------------
HORIZON_DAYS: Final = 364  # a plan covers 52 weeks from tomorrow at most

# --- Macrocycle, backwards from each A race ----------------------------------------------------
SHORT_RACE_MAX_M: Final = 10_000  # up to a 10 km
HALF_MARATHON_M: Final = 21_097  # up to the half marathon
# Stepped taper before an A race (KNO24, HAU22): one step per week counted back from the race,
# each the share of the reference week's load (the realised load of the last loaded week).
# Marathon and beyond: 21 days, about -25% a week and about -50% in the last week; half marathon:
# 14 days, -25% then -45% (project default between the two); up to 10 km: 7 days, -40%.
TAPER_FRACTIONS: Final[dict[RaceClass, tuple[float, ...]]] = {
    RaceClass.SHORT: (0.60,),
    RaceClass.HALF: (0.75, 0.55),
    RaceClass.LONG: (0.75, 0.625, 0.50),
}
TAPER_STEP_DAYS: Final = 7
TAPER_DAYS: Final = {c: TAPER_STEP_DAYS * len(f) for c, f in TAPER_FRACTIONS.items()}
TAPER_DAYS_SHORT: Final = TAPER_DAYS[RaceClass.SHORT]
TAPER_DAYS_HALF: Final = TAPER_DAYS[RaceClass.HALF]
TAPER_DAYS_LONG: Final = TAPER_DAYS[RaceClass.LONG]  # marathon and beyond
RECOVERY_DAYS_SHORT: Final = 3  # easy days after an A race
RECOVERY_DAYS_HALF: Final = 5
RECOVERY_DAYS_LONG: Final = 7
RECOVERY_DAYS: Final = {
    RaceClass.SHORT: RECOVERY_DAYS_SHORT,
    RaceClass.HALF: RECOVERY_DAYS_HALF,
    RaceClass.LONG: RECOVERY_DAYS_LONG,
}
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
UNLOAD_FRACTION: Final = 0.70  # unload week and B-race mini taper: -30% (SAN25: -25 to -35%)
MIN_LOADED_DAYS_IN_WEEK: Final = 4  # a week with fewer is not counted in the 3:1 cycle

# --- Week --------------------------------------------------------------------------------------
MAX_INTENSE_PER_WEEK: Final = 3
MAX_INTENSE_PER_WEEK_BASE: Final = 2
LOW_ZONE_SHARE_PERCENT: Final = 80  # 80/20 by time: at least this much in Z1-Z2 (base, maintenance)
# 80/20 by sessions too (TON24: low-intensity sessions are 75-80% of the sessions): at most this
# share of the week's sessions has an intense goal (threshold, VO2max, medium; the long run is not).
MAX_INTENSE_SESSION_SHARE_PERCENT: Final = 25
# ...but a week of at least this many sessions may always have one (KNO24: 1-2 high-intensity
# sessions for amateurs); with fewer there is none.
MIN_SESSIONS_FOR_INTENSE: Final = 3
# Hard days (intense sessions and the long run) in a week (SAN25: 2-3 key days); with fewer than
# FEW_SESSIONS sessions at most MAX_INTENSE_FEW_SESSIONS intense (KNO24: 1-2 HIT for amateurs).
MAX_HARD_DAYS_PER_WEEK: Final = 3
FEW_SESSIONS: Final = 6
MAX_INTENSE_FEW_SESSIONS: Final = 2
# In the taper the sessions keep their number; one quality session a week, shortened (TON14,
# SAN25), never in the last 72 hours before a race (project default).
TAPER_QUALITY_MAX_MINUTES: Final = 45
NO_QUALITY_DAYS_BEFORE_RACE: Final = 3
ROUND_MINUTES: Final = 5  # sessions last a multiple of this
TAPER_EVE_MAX_MINUTES: Final = 30  # the session of the day before a race
RECOVERY_SESSION_MINUTES: Final = 30
SCALE_ATTEMPTS: Final = 24  # tries to fit a week in the ramp and hours limits

# Intense work in a week, in the order it is scheduled (and dropped in reverse for the 80/20).
# With several types the week takes one in turn (they alternate week after week).
WEEK_QUALITY: Final[dict[Phase, tuple[SessionType, ...]]] = {
    Phase.MAINTENANCE: (SessionType.THRESHOLD,),
    Phase.BASE: (SessionType.THRESHOLD, SessionType.MEDIUM),
    Phase.BUILD: (SessionType.THRESHOLD, SessionType.VO2MAX),
    Phase.PEAK: (SessionType.THRESHOLD, SessionType.VO2MAX),  # up to 10 km: they alternate (MUN15)
}
# Quality of the peak for a half marathon or longer, and of the taper for every A race, in order of
# preference among the chosen types: no VO2max in the peak of a long race; the race-pace work goes
# in the "medium" (marathon pace) or the "threshold" (half-marathon pace) sessions (MUN15). Up to
# 10 km the taper keeps the race-pace threshold work, and VO2max if threshold is not chosen.
RACE_QUALITY: Final[dict[RaceClass, tuple[SessionType, ...]]] = {
    RaceClass.SHORT: (SessionType.THRESHOLD, SessionType.VO2MAX),
    RaceClass.HALF: (SessionType.THRESHOLD, SessionType.MEDIUM),
    RaceClass.LONG: (SessionType.MEDIUM, SessionType.THRESHOLD),
}
# The session type whose work blocks are at race pace in the peak and the taper, and their zone:
# marathon pace is zone 3 (medium), half-marathon and 10 km pace zone 4 (threshold).
RACE_PACE_SESSION: Final[dict[RaceClass, SessionType]] = {
    RaceClass.SHORT: SessionType.THRESHOLD,
    RaceClass.HALF: SessionType.THRESHOLD,
    RaceClass.LONG: SessionType.MEDIUM,
}
RACE_PACE_ZONE: Final[dict[RaceClass, int]] = {
    RaceClass.SHORT: 4,
    RaceClass.HALF: 4,
    RaceClass.LONG: 3,
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
# The long session is the key session of the week and has to be the longest by minutes: an easy,
# recovery, medium or alternative session is always shorter than it, and an easy or recovery one
# lasts at most this share of it (a long run of 60 minutes next to an easy run of 90 would turn the
# week upside down). When the hours or the load do not allow all, the others give way first.
OTHER_SESSION_MAX_SHARE_OF_LONG_PERCENT: Final = 75

# The most a day can hold: the longest session of any type (the user gives no minutes per day).
DAY_MAX_MINUTES: Final = max(MAX_MINUTES.values())

# --- Threshold test in the plan (ADR 015) -----------------------------------------------------
# The test (FRI, FRI5: at the start of a period) is a hard session: warm-up, the test, cool-down.
TEST_WARMUP_MINUTES: Final = 15
TEST_COOLDOWN_MINUTES: Final = 10
TEST_ZONE: Final = 4  # the zone the test effort is expected in (FRI: 30' mean HR of the last 20')
# The weeks, from the first one that can take it, in which the engine tries to place the test: a
# week with no day that fits (a short first week, a race in it) leaves a second chance.
TEST_ATTEMPT_WEEKS: Final = 2
# The test is planned at most this many times: a test that went by without being done is
# proposed once more, the week after, and then the plan stops asking.
TEST_MAX_PLANNED: Final = 2

# --- Missed sessions -----------------------------------------------------------------------------
# A planned session counts as done when the load of its day reaches this share of the session's
# load; below it, a long or intense session is missed and made up in the days left in its week.
DONE_LOAD_SHARE_PERCENT: Final = 50

# --- Races of another sport ----------------------------------------------------------------------
# Up to a race of another sport than the plan's (a B or C race, or a later A race), one easy
# session in `OTHER_SPORT_EASY_EVERY` is in that sport; in the last `OTHER_SPORT_LONG_DAYS` days
# before it, the long session too. The intense sessions stay in the plan's sport.
OTHER_SPORT_EASY_EVERY: Final = 2
OTHER_SPORT_LONG_DAYS: Final = 42

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
    # Shortened quality of the taper: a short warm-up and cool-down, fixed number of repetitions.
    (SessionType.THRESHOLD, Phase.TAPER): IntervalModel(10, 10, 8, 2, 4, 2, 2),
    (SessionType.VO2MAX, Phase.TAPER): IntervalModel(10, 10, 3, 3, 5, 3, 3),
}
# Hill repeats replace one quality session of the build and the peak of a hilly race: the work is
# run uphill and the rest is the jog back down. Short and hard (zone 5) in the build; longer and
# at the threshold (zone 4) in the peak. No pace target on a slope: heart rate only.
HILL_REPEAT_MODELS: Final[dict[Phase, IntervalModel]] = {
    Phase.BUILD: IntervalModel(15, 10, 2, 3, 5, 6, 10),
    Phase.PEAK: IntervalModel(15, 10, 3, 3, 4, 4, 8),
}

# Heart rate that stands for a zone when estimating a session's load, in percent of the threshold,
# inside the zone of `load.ZONE_STARTS_PERCENT` (Z1 50-71, Z2 72-84, Z3 85-94, Z4 95-102, Z5 from
# 103): as before, a point in the part of the zone where the sessions of that zone are run: Z1 66
# (upper part: recovery and warm-up jogging), Z2 82 (upper part: easy running), Z3 90 (middle),
# Z4 97 (lower-middle), and the lower bound for Z5, which is open towards the maximum HR (its
# middle is unknown; the bound keeps the estimate conservative). Only Z1 moves (was 70): the
# others were already inside the new zones, so the estimated loads stay close to the old ones.
ZONE_REPRESENTATIVE_PERCENT: Final = (66, 82, 90, 97, 103)

# Race effort: zone shares by expected duration, (up to minutes, shares of Z1..Z5).
RACE_ZONE_MIX: Final = (
    (60, (0.0, 0.0, 0.2, 0.6, 0.2)),
    (150, (0.0, 0.0, 0.3, 0.7, 0.0)),
    (10**9, (0.0, 0.4, 0.6, 0.0, 0.0)),
)
DEFAULT_RACE_PACE_S_PER_KM: Final = 360  # to size a race's load when nothing else is known

# --- Elevation gain (project defaults) ---------------------------------------------------------
# A metre of climb counts as this many metres of flat running: 100 m of climb are 1 km, the rule
# of thumb behind the "km-effort" of trail races. The race's time, pace and load are computed on
# the flat-equivalent distance, `distance + ascent * ASCENT_EQUIVALENT_M_PER_M`.
ASCENT_EQUIVALENT_M_PER_M: Final = 10
# An A race whose mean climb is at least this many metres per kilometre is a hilly race: its
# build and peak have hill work. 20 m/km is 200 m in 10 km or 420 m in a half marathon, well above
# a road race (a flat half marathon has 50 to 150 m) and below the mountain races (UTMB about
# 58 m/km).
HILL_MIN_SLOPE_M_PER_KM: Final = 20
# The phases whose weeks hold hill work: the specific preparation, never the base, the taper or an
# unload week.
HILL_PHASES: Final = frozenset({Phase.BUILD, Phase.PEAK})
# Indicative speed of a hilly long run (km/h), so that its elevation gain follows the race's mean
# climb: gain = hours * speed * slope.
HILLY_LONG_KM_PER_HOUR: Final = 7
# Indicative climb of a minute of hill-repeat work (about an 8% grade at 7.5 km/h).
HILL_REPEAT_ASCENT_M_PER_MIN: Final = 10
HILL_ASCENT_ROUND_M: Final = 10  # the indicative gain of a session is a multiple of this

# --- Cycling races (project defaults) ----------------------------------------------------------
# A cycling race has no target time: its time is estimated at an amateur's flat speed, with every
# metre of climb counted as this many flat metres (at 25 km/h, 1000 m of climb add one hour, the
# usual rule of thumb of cyclists).
CYCLING_FLAT_KM_PER_HOUR: Final = 25
CYCLING_ASCENT_EQUIVALENT_M_PER_M: Final = 25
# The rules written for running distances (class and taper, recovery, CTL to reach, long session)
# take a cycling race as the run of equal duration at this pace: 1 hour of riding is a 10 km.
EFFORT_PACE_S_PER_KM: Final = DEFAULT_RACE_PACE_S_PER_KM

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
    "max_intense_session_share_percent",
    "min_sessions_for_intense",
    "max_hard_days_per_week",
    "few_sessions",
    "max_intense_few_sessions",
    "taper_quality_max_minutes",
    "no_quality_days_before_race",
    "taper_fraction_short",
    "taper_fraction_half",
    "taper_fraction_long",
    "recovery_tsb",
    "recovery_atl_ctl_ratio",
    "recovery_days_adaptation",
    "riegel_exponent",
    "ambitious_margin_percent",
    "test_warmup_minutes",
    "test_cooldown_minutes",
    "test_attempt_weeks",
    "test_max_planned",
    "ascent_equivalent_m_per_m",
    "hill_min_slope_m_per_km",
    "cycling_flat_km_per_hour",
    "cycling_ascent_equivalent_m_per_m",
    "effort_pace_s_per_km",
    "done_load_share_percent",
    "other_sport_easy_every",
    "other_sport_long_days",
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
        MAX_INTENSE_SESSION_SHARE_PERCENT,
        MIN_SESSIONS_FOR_INTENSE,
        MAX_HARD_DAYS_PER_WEEK,
        FEW_SESSIONS,
        MAX_INTENSE_FEW_SESSIONS,
        TAPER_QUALITY_MAX_MINUTES,
        NO_QUALITY_DAYS_BEFORE_RACE,
        TAPER_FRACTIONS[RaceClass.SHORT][-1],
        TAPER_FRACTIONS[RaceClass.HALF][-1],
        TAPER_FRACTIONS[RaceClass.LONG][-1],
        RECOVERY_TSB,
        RECOVERY_ATL_CTL_RATIO,
        RECOVERY_DAYS_ADAPTATION,
        RIEGEL_EXPONENT,
        AMBITIOUS_MARGIN_PERCENT,
        TEST_WARMUP_MINUTES,
        TEST_COOLDOWN_MINUTES,
        TEST_ATTEMPT_WEEKS,
        TEST_MAX_PLANNED,
        ASCENT_EQUIVALENT_M_PER_M,
        HILL_MIN_SLOPE_M_PER_KM,
        CYCLING_FLAT_KM_PER_HOUR,
        CYCLING_ASCENT_EQUIVALENT_M_PER_M,
        EFFORT_PACE_S_PER_KM,
        DONE_LOAD_SHARE_PERCENT,
        OTHER_SPORT_EASY_EVERY,
        OTHER_SPORT_LONG_DAYS,
    )
    return tuple((name, float(value)) for name, value in zip(PARAMETER_NAMES, values, strict=True))
