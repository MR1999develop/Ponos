# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity read from a FIT or GPX file."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ponos_coach.activities.domain.distance_series import DistanceSeries
from ponos_coach.activities.domain.heart_rate_series import HeartRateSeries
from ponos_coach.activities.domain.identity import ActivityIdentity, FileFormat, format_of
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.summary import ActivitySummary
from ponos_coach.activities.domain.timing import (
    check_durations,
    check_utc,
    is_plausible_span,
)


@dataclass(frozen=True, slots=True)
class Activity:
    """An activity: identity, format, sport, UTC start, durations, HR series and summary.

    `elapsed_s` is the elapsed time (pauses included) and `timer_s` the moving time
    (without pauses); both finite and positive, with `timer_s <= elapsed_s` (project
    rule: whoever reads the file brings the timer up to the elapsed time within 1 s and rejects the
    rest).
    `file_format` must be that of the identity. The HR series has seconds from the first
    record; `discarded_hr_samples` counts the HR samples discarded because of
    repeated or non-increasing timestamps. The optional distance series has the same time
    axis (seconds from the first record); it is absent when the file has no usable distance.
    `first_record_offset_s` is the time from `start` to that first record (a watch records a few
    seconds after the session start): the origin of the series is `start + first_record_offset_s`.
    Start and end lie in [1990-01-01, 2100-01-01) UTC.
    """

    identity: ActivityIdentity
    file_format: FileFormat
    sport: Sport
    start: datetime
    elapsed_s: float
    timer_s: float
    heart_rate: HeartRateSeries | None
    summary: ActivitySummary
    discarded_hr_samples: int = 0
    distance: DistanceSeries | None = None
    first_record_offset_s: float = 0.0

    def __post_init__(self) -> None:
        if format_of(self.identity) is not self.file_format:
            raise ValueError("the format does not match the identity")
        check_utc(self.start)
        check_durations(self.elapsed_s, self.timer_s)
        if not is_plausible_span(self.start, self.elapsed_s):
            raise ValueError("start or end outside the window of allowed times")
        if not math.isfinite(self.first_record_offset_s):
            raise ValueError("the offset of the first record must be finite")
        if self.discarded_hr_samples < 0:
            raise ValueError("discarded samples cannot be negative")
