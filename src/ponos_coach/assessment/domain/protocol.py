# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The two threshold test protocols and the numbers that tell them apart."""

from __future__ import annotations

from enum import Enum

from ponos_coach.assessment.domain.defaults import (
    THIRTY_MINUTES_S,
    THRESHOLD_WINDOW_S,
    TWENTY_MINUTES_S,
)


class ThresholdProtocol(Enum):
    """How the test is run. The value is stable (it is saved with every test).

    `THIRTY_MINUTES` (FRI, editions before the 5th): 30 minutes at the greatest sustainable
    effort; the threshold is the mean HR (and the pace) of the last 20 minutes.
    `TWENTY_MINUTES` (FRI5, 5th edition): 20 minutes; the threshold HR is the mean HR of the
    whole segment times 0.95 and the threshold pace is the mean pace plus 5% of the time per
    kilometre.
    """

    THIRTY_MINUTES = "thirty_minutes"
    TWENTY_MINUTES = "twenty_minutes"

    @property
    def duration_s(self) -> int:
        """Length of the segment, in seconds of elapsed time."""
        return THIRTY_MINUTES_S if self is ThresholdProtocol.THIRTY_MINUTES else TWENTY_MINUTES_S

    @property
    def window_s(self) -> int:
        """Length of the closing stretch that gives the HR and the pace (always 20 minutes)."""
        return THRESHOLD_WINDOW_S


DEFAULT_PROTOCOL = ThresholdProtocol.THIRTY_MINUTES
