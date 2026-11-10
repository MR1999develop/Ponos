# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Heart-rate types: limits, threshold, sex."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.activities.domain import Sport

# Allowed ranges (project default): they discard obviously wrong values.
_MIN_BPM_LOW = 20
_MAX_BPM_HIGH = 250
BIKE_THRESHOLD_OFFSET_BPM = 4  # project default: bike threshold = run threshold - 4


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
    """The sports that share one threshold heart rate.

    Running and trail are one group (the threshold is the running one); cycling has its own
    because the heart rate at the same effort is lower on the bike; everything else (walking,
    hiking, swimming-like or indoor activity, strength, other) shares one more.
    """

    RUNNING = "running"
    CYCLING = "cycling"
    OTHER = "other"


_SPORT_GROUPS = {
    Sport.RUNNING: SportGroup.RUNNING,
    Sport.TRAIL: SportGroup.RUNNING,
    Sport.CYCLING: SportGroup.CYCLING,
}


def sport_group(sport: Sport) -> SportGroup:
    """The threshold group of a sport: running (also trail), cycling, or other."""
    return _SPORT_GROUPS.get(sport, SportGroup.OTHER)


def threshold_for_sport(physiology: Physiology, sport: Sport) -> int:
    """Threshold used for the sport: the one entered for its group, else the default.

    Running (and trail): the running threshold. Cycling: the cycling threshold if entered, else
    `max(run_threshold - 4, min_HR + 1)` (brought to the limit instead of rejecting the
    parameters; project default). Other sports: the other-activities threshold if entered, else
    the running one. The only place where a sport's threshold is decided.
    """
    group = sport_group(sport)
    if group is SportGroup.CYCLING:
        if physiology.bike_threshold is not None:
            return physiology.bike_threshold.bpm
        return max(
            physiology.threshold.bpm - BIKE_THRESHOLD_OFFSET_BPM, physiology.limits.min_bpm + 1
        )
    if group is SportGroup.OTHER and physiology.other_threshold is not None:
        return physiology.other_threshold.bpm
    return physiology.threshold.bpm


@dataclass(frozen=True, slots=True)
class Physiology:
    """User's physiological parameters, consistent with one another by construction.

    The running threshold lies between min HR (excluded) and max HR (included). The cycling and
    other-activities thresholds are optional (`None`: the default of `threshold_for_sport`) and,
    when entered, lie in the same range. Read thresholds only through `threshold_for_sport`.
    """

    limits: HeartRateLimits
    threshold: ThresholdHeartRate
    sex: Sex
    bike_threshold: ThresholdHeartRate | None = None
    other_threshold: ThresholdHeartRate | None = None

    def __post_init__(self) -> None:
        for entered in (self.threshold, self.bike_threshold, self.other_threshold):
            if entered is not None and not self.limits.min_bpm < entered.bpm <= self.limits.max_bpm:
                raise ValueError("the threshold must lie between min HR and max HR")
