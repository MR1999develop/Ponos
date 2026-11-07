# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synthetic runs for the threshold test: stretches at a constant HR and speed, one record a second.

A run is a list of `Leg`s. Distance is the integral of the speed (zero while records are dropped
for a pause), so the expected segment, HR and pace of a test can be computed by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from ponos_coach.activities.domain import (
    Activity,
    ActivitySummary,
    DistanceSeries,
    FileFormat,
    HeartRateSeries,
    Sport,
)
from ponos_coach.load.domain import HeartRateLimits
from tests.builders import T0, fit_identity

LIMITS = HeartRateLimits(50, 190)
OUT_OF_RANGE_BPM = 0  # below min HR: a sample the test discards


@dataclass(frozen=True)
class Leg:
    """A stretch of the run: `seconds` long, at `bpm` (None: no HR record) and `speed_ms`."""

    seconds: int
    bpm: int | None
    speed_ms: float = 3.0


def synthetic_run(
    legs: Sequence[Leg],
    *,
    sport: Sport = Sport.RUNNING,
    number: int = 1,
    pauses: Sequence[tuple[int, int]] = (),
    with_distance: bool = True,
    with_heart_rate: bool = True,
    elapsed_s: float | None = None,
) -> Activity:
    """The run described by `legs`.

    `pauses` are `(after_s, gap_s)`: no record is written between second `after_s` and second
    `after_s + gap_s`, so consecutive records are `gap_s` apart and no distance is covered.
    The elapsed time is the total of the legs.
    """
    bpm_leg = np.concatenate([np.full(leg.seconds, np.nan if leg.bpm is None else leg.bpm)
                              for leg in legs])  # fmt: skip
    speed = np.concatenate([np.full(leg.seconds, leg.speed_ms) for leg in legs])
    # One record at each second from 0 to the end of the last leg (included): the records span
    # exactly the time of the legs, and the distance at a record is what was covered before it.
    bpm_by_second = np.append(bpm_leg, bpm_leg[-1])
    seconds = np.arange(bpm_by_second.size, dtype=np.float64)
    keep = np.ones(seconds.size, dtype=bool)
    for after_s, gap_s in pauses:
        keep[(seconds > after_s) & (seconds < after_s + gap_s)] = False
    speed = np.where(keep[:-1], speed, 0.0)
    meters = np.concatenate(([0.0], np.cumsum(speed)))
    has_hr = keep & ~np.isnan(bpm_by_second)
    total = float(seconds.size - 1) if elapsed_s is None else elapsed_s
    return Activity(
        fit_identity(number),
        FileFormat.FIT,
        sport,
        T0,
        total,
        total - float(np.count_nonzero(~keep)),
        HeartRateSeries(seconds[has_hr], bpm_by_second[has_hr]) if with_heart_rate else None,
        ActivitySummary(),
        0,
        DistanceSeries(seconds[keep], meters[keep]) if with_distance else None,
    )


def standard_test(
    *, warm_up_s: int = 600, hr: int = 165, speed_ms: float = 3.5, cool_down_s: int = 300
) -> list[Leg]:
    """Warm-up (easy), 30 minutes at `hr` and `speed_ms`, cool-down (easy)."""
    return [
        Leg(warm_up_s, 130, 3.0),
        Leg(1800, hr, speed_ms),
        Leg(cool_down_s, 120, 2.5),
    ]
