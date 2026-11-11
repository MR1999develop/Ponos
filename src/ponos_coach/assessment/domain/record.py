# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A threshold test applied to the profile: what the history keeps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.activities.domain.timing import check_utc
from ponos_coach.assessment.domain.defaults import (
    MAX_PACE_S_PER_KM,
    METHOD_VERSION,
    MIN_PACE_S_PER_KM,
)
from ponos_coach.assessment.domain.protocol import DEFAULT_PROTOCOL, ThresholdProtocol
from ponos_coach.assessment.domain.threshold_test import ThresholdTestResult

MAX_THRESHOLD_BPM = 250
MIN_THRESHOLD_BPM = 40


@dataclass(frozen=True, slots=True)
class ThresholdTest:
    """A test in the history: the activity (kept even if the index is rebuilt), its local day,
    the start of the segment (UTC), the threshold HR, the pace in s/km or `None`, and the version
    of the method that computed them, and the protocol the test was run with.

    Invariants: UTC start, HR between 40 and 250 bpm, pace within the plausible range, method
    version at least 1.
    """

    identity: ActivityIdentity
    test_day: date
    segment_start: datetime
    threshold_bpm: int
    pace_s_per_km: int | None
    method_version: int = METHOD_VERSION
    protocol: ThresholdProtocol = DEFAULT_PROTOCOL

    def __post_init__(self) -> None:
        check_utc(self.segment_start)
        if not MIN_THRESHOLD_BPM <= self.threshold_bpm <= MAX_THRESHOLD_BPM:
            raise ValueError("threshold HR outside [40, 250] bpm")
        if self.pace_s_per_km is not None and not (
            MIN_PACE_S_PER_KM <= self.pace_s_per_km <= MAX_PACE_S_PER_KM
        ):
            raise ValueError("pace outside the plausible range")
        if self.method_version < 1:
            raise ValueError("the method version starts at 1")

    @classmethod
    def of(cls, result: ThresholdTestResult) -> ThresholdTest:
        """The history entry of an accepted test."""
        return cls(
            result.identity,
            result.test_day,
            result.segment_start,
            result.threshold_bpm,
            result.pace_s_per_km,
            result.method_version,
            result.protocol,
        )
