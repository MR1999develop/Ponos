# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What a race asks of the athlete: the CTL to reach and whether the target time is possible."""

from __future__ import annotations

from bisect import bisect_right
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import RaceClass


class TargetClass(Enum):
    """How far a target time is from what the athlete can do by the race."""

    REALISTIC = "realistic"
    AMBITIOUS = "ambitious"
    UNREACHABLE = "unreachable"
    NOT_ESTIMABLE = "not_estimable"  # no threshold pace, or not a running race


def target_ctl_for_distance(distance_m: int) -> float:
    """CTL to reach for an A race of this distance (table of the project, linear in between)."""
    points = defaults.CTL_BY_DISTANCE
    if distance_m <= points[0][0]:
        return points[0][1]
    if distance_m >= points[-1][0]:
        return points[-1][1]
    index = bisect_right([distance for distance, _ in points], distance_m)
    (d0, c0), (d1, c1) = points[index - 1], points[index]
    return c0 + (c1 - c0) * (distance_m - d0) / (d1 - d0)


def race_class_for(distance_m: int) -> RaceClass:
    """Length class of a race of this distance (the half marathon belongs to `HALF`)."""
    if distance_m <= defaults.SHORT_RACE_MAX_M:
        return RaceClass.SHORT
    if distance_m <= defaults.HALF_MARATHON_M:
        return RaceClass.HALF
    return RaceClass.LONG


def taper_days_for(distance_m: int) -> int:
    """Days of final unload before an A race of this distance."""
    return defaults.TAPER_DAYS[race_class_for(distance_m)]


def recovery_days_for(distance_m: int) -> int:
    """Easy days after an A race of this distance."""
    return defaults.RECOVERY_DAYS[race_class_for(distance_m)]


def equivalent_distance_m(distance_m: int, ascent_m: int) -> int:
    """The flat distance that asks as much as a race of `distance_m` with `ascent_m` of climb:
    every metre of climb counts as `ASCENT_EQUIVALENT_M_PER_M` metres of flat running. A flat race
    (no climb) keeps its own distance."""
    return distance_m + ascent_m * defaults.ASCENT_EQUIVALENT_M_PER_M


def cycling_time_s(distance_m: int, ascent_m: int) -> int:
    """Estimated time of a cycling race, whole seconds: its flat-equivalent distance (a metre of
    climb counts as `CYCLING_ASCENT_EQUIVALENT_M_PER_M` flat metres) at the speed
    `CYCLING_FLAT_KM_PER_HOUR`."""
    flat_m = distance_m + ascent_m * defaults.CYCLING_ASCENT_EQUIVALENT_M_PER_M
    return max(1, round(flat_m * 3600 / (defaults.CYCLING_FLAT_KM_PER_HOUR * 1000)))


def effort_distance_m(time_s: int) -> int:
    """The running distance that lasts `time_s` at `EFFORT_PACE_S_PER_KM`: how the rules written
    for running distances (class, taper, recovery, CTL to reach) see a race of another sport."""
    return max(1, round(time_s * 1000 / defaults.EFFORT_PACE_S_PER_KM))


def rule_distance_m(sport: Sport, distance_m: int, ascent_m: int) -> int:
    """The distance the rules written for running races (class, taper, recovery, CTL to reach,
    long session, peak quality) go by: the race's own, or for a cycling race the run of equal
    duration."""
    if sport is Sport.CYCLING:
        return effort_distance_m(cycling_time_s(distance_m, ascent_m))
    return distance_m


def predicted_time_today_s(threshold_pace_s_per_km: int, distance_m: int) -> float:
    """Time predicted today for the distance, by Riegel's formula.

    The reference effort is the distance run in one hour at the threshold pace:
    `T = 3600 * (distance / hour_distance) ** 1.06`.
    """
    hour_distance_m = defaults.THRESHOLD_HOUR_S * 1000 / threshold_pace_s_per_km
    ratio = distance_m / hour_distance_m
    return float(defaults.THRESHOLD_HOUR_S * ratio**defaults.RIEGEL_EXPONENT)


def best_time_by_race_s(threshold_pace_s_per_km: int, distance_m: int, days_to_race: int) -> float:
    """Best time possible at the race: today's prediction improved by 0.5% a week, at most 8%."""
    permille = min(
        float(defaults.MAX_IMPROVEMENT_PERMILLE),
        defaults.IMPROVEMENT_PERMILLE_PER_WEEK * max(days_to_race, 0) / 7,
    )
    return predicted_time_today_s(threshold_pace_s_per_km, distance_m) * (1 - permille / 1000)


def classify_target(target_time_s: int, best_time_s: float) -> TargetClass:
    """Realistic if the target is not faster than the best time; ambitious if it is within a
    further 3% of it; otherwise unreachable. The boundaries belong to the easier class."""
    if target_time_s >= best_time_s:
        return TargetClass.REALISTIC
    margin = 100 - defaults.AMBITIOUS_MARGIN_PERCENT
    if target_time_s * 100 >= best_time_s * margin:
        return TargetClass.AMBITIOUS
    return TargetClass.UNREACHABLE
