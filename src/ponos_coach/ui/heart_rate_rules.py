# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules for the three HR values (minimum, maximum, threshold) that forms check in place.

A single source for "New profile" and "Edit profile": the domain enforces them anyway (a test
checks that the ranges match); here we only say which rule is violated, in categories.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QCoreApplication

# Control ranges; the domain enforces them (a test checks that they match).
MIN_ALLOWED_BPM = 20
MAX_ALLOWED_BPM = 250
MIN_THRESHOLD_BPM = 40


class HeartRateProblem(Enum):
    """Which rule on the three HR values is violated."""

    MIN_NOT_BELOW_MAX = "min_not_below_max"
    THRESHOLD_NOT_ABOVE_MIN = "threshold_not_above_min"
    THRESHOLD_ABOVE_MAX = "threshold_above_max"


def heart_rate_problems(
    min_bpm: int, max_bpm: int, threshold_bpm: int
) -> tuple[HeartRateProblem, ...]:
    """Rules violated by minimum, maximum and threshold HR (empty if the three values are
    consistent)."""
    found: list[HeartRateProblem] = []
    if min_bpm >= max_bpm:
        found.append(HeartRateProblem.MIN_NOT_BELOW_MAX)
    if threshold_bpm <= min_bpm:
        found.append(HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN)
    if threshold_bpm > max_bpm:
        found.append(HeartRateProblem.THRESHOLD_ABOVE_MAX)
    return tuple(found)


def heart_rate_problem_text(problem: HeartRateProblem) -> str:
    """Message under the field: which rule fails and how to fix it."""
    texts = {
        HeartRateProblem.MIN_NOT_BELOW_MAX: QCoreApplication.translate(
            "HeartRate", "The resting heart rate must be lower than the maximum."
        ),
        HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: QCoreApplication.translate(
            "HeartRate", "The threshold must be higher than the resting heart rate."
        ),
        HeartRateProblem.THRESHOLD_ABOVE_MAX: QCoreApplication.translate(
            "HeartRate", "The threshold cannot be higher than the maximum."
        ),
    }
    return texts[problem]
