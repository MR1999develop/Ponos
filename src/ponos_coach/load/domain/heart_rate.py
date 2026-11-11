# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Heart-rate types: limits, lactate thresholds (LT1, LT2), sex."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport

# Allowed ranges (project default): they discard obviously wrong values.
_MIN_BPM_LOW = 20
_MAX_BPM_HIGH = 250
DEFAULT_LT1_PERCENT = 83  # project default: LT1 not entered = 83% of the LT2 of its group


class Sex(Enum):
    """Sex of the profile; determines Banister's coefficient k."""

    MALE = "male"
    FEMALE = "female"

    @property
    def banister_k(self) -> float:
        """Coefficient k of Banister's exponential: 1.92 (male), 1.67 (female)."""
        return 1.92 if self is Sex.MALE else 1.67


@dataclass(frozen=True, slots=True)
class HeartRateLimits:
    """Minimum (resting) and maximum HR, in bpm; `20 <= min < max <= 250`."""

    min_bpm: int
    max_bpm: int

    def __post_init__(self) -> None:
        if not _MIN_BPM_LOW <= self.min_bpm < self.max_bpm <= _MAX_BPM_HIGH:
            raise ValueError(f"need {_MIN_BPM_LOW} <= min HR < max HR <= {_MAX_BPM_HIGH}")

    @property
    def reserve_bpm(self) -> int:
        """Heart-rate reserve `max - min`, always positive."""
        return self.max_bpm - self.min_bpm


@dataclass(frozen=True, slots=True)
class ThresholdHeartRate:
    """A threshold HR, in bpm, between 40 and 250."""

    bpm: int

    def __post_init__(self) -> None:
        if not 40 <= self.bpm <= _MAX_BPM_HIGH:
            raise ValueError("threshold HR outside [40, 250] bpm")


class SportGroup(Enum):
    """The two groups of sports that share the lactate thresholds (LT1 and LT2).

    Running and trail are one group; everything else (cycling, walking, hiking, swimming-like or
    indoor activity, strength, other) is the other group.
    """

    RUNNING = "running"
    OTHER = "other"


_SPORT_GROUPS = {
    Sport.RUNNING: SportGroup.RUNNING,
    Sport.TRAIL: SportGroup.RUNNING,
}


def sport_group(sport: Sport) -> SportGroup:
    """The threshold group of a sport: running (also trail), or other (cycling included)."""
    return _SPORT_GROUPS.get(sport, SportGroup.OTHER)


def threshold_for_sport(physiology: Physiology, sport: Sport) -> int:
    """LT2 used for the sport: the one entered for its group, else the running one.

    Running (and trail): the running LT2 (`Physiology.threshold`). Every other sport, cycling
    included: the other-sports LT2 if entered, else the running one. The only place where the
    LT2 of a sport is decided; the zones, the TRIMPeq and the plan all read it from here.
    """
    if sport_group(sport) is SportGroup.OTHER and physiology.other_threshold is not None:
        return physiology.other_threshold.bpm
    return physiology.threshold.bpm


def default_lt1(lt2_bpm: int, min_bpm: int) -> int:
    """LT1 assumed when none is entered: `DEFAULT_LT1_PERCENT` of `lt2_bpm`, rounded half up.

    Brought to the limits instead of rejecting the parameters: at least `min_bpm + 1` and never
    above `lt2_bpm` (the two coincide only if the LT2 is one bpm above the minimum).
    """
    rounded = (lt2_bpm * DEFAULT_LT1_PERCENT + 50) // 100
    return min(max(rounded, min_bpm + 1), lt2_bpm)


def lt1_for_sport(physiology: Physiology, sport: Sport) -> int:
    """LT1 used for the sport: the one entered for its group, else the default from its LT2.

    The twin of `threshold_for_sport`, and the only place where the LT1 of a sport is decided.
    """
    group = sport_group(sport)
    entered = physiology.run_lt1 if group is SportGroup.RUNNING else physiology.other_lt1
    if entered is not None:
        return entered.bpm
    return default_lt1(threshold_for_sport(physiology, sport), physiology.limits.min_bpm)


@dataclass(frozen=True, slots=True)
class Physiology:
    """User's physiological parameters, consistent with one another by construction.

    `threshold` is the running LT2 (lactate threshold) and `other_threshold` the other-sports
    LT2 (`None`: the running one applies); `run_lt1` and `other_lt1` are the aerobic thresholds
    of the two groups (`None`: `default_lt1` applies). Every LT2 lies between min HR (excluded)
    and max HR (included); an entered LT1 lies between min HR and the LT2 of its group, both
    excluded: `min HR < LT1 < LT2 <= max HR`. Read them only through `threshold_for_sport` and
    `lt1_for_sport`.
    """

    limits: HeartRateLimits
    threshold: ThresholdHeartRate
    sex: Sex
    _: KW_ONLY
    other_threshold: ThresholdHeartRate | None = None
    run_lt1: ThresholdHeartRate | None = None
    other_lt1: ThresholdHeartRate | None = None

    def __post_init__(self) -> None:
        low, high = self.limits.min_bpm, self.limits.max_bpm
        for lt2 in (self.threshold, self.other_threshold):
            if lt2 is not None and not low < lt2.bpm <= high:
                raise ValueError("the threshold must lie between min HR and max HR")
        other_lt2 = self.threshold if self.other_threshold is None else self.other_threshold
        for lt1, lt2 in ((self.run_lt1, self.threshold), (self.other_lt1, other_lt2)):
            if lt1 is not None and not low < lt1.bpm < lt2.bpm:
                raise ValueError("LT1 must lie between min HR and LT2")
