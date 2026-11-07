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
        """Coefficient k of the TRIMP exponential: 1.92 (male), 1.67 (female)."""
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
    """Running threshold HR, in bpm, between 40 and 250."""

    bpm: int

    def __post_init__(self) -> None:
        if not 40 <= self.bpm <= _MAX_BPM_HIGH:
            raise ValueError("threshold HR outside [40, 250] bpm")


def threshold_for_sport(physiology: Physiology, sport: Sport) -> int:
    """Threshold used for the sport.

    On the bike it is `max(run_threshold - 4, min_HR + 1)`: the value is brought to the limit
    instead of
    rejecting the parameters (project default); in the other sports it is the running threshold.
    """
    threshold = physiology.threshold.bpm
    if sport is Sport.CYCLING:
        return max(threshold - BIKE_THRESHOLD_OFFSET_BPM, physiology.limits.min_bpm + 1)
    return threshold


@dataclass(frozen=True, slots=True)
class Physiology:
    """User's physiological parameters, consistent with one another by construction.

    The running threshold lies between min HR (excluded) and max HR (included); the
    bike one is derived with `threshold_for_sport` and does not constrain the constructor.
    """

    limits: HeartRateLimits
    threshold: ThresholdHeartRate
    sex: Sex

    def __post_init__(self) -> None:
        if not self.limits.min_bpm < self.threshold.bpm <= self.limits.max_bpm:
            raise ValueError("the threshold must lie between min HR and max HR")
