# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules for the three HR values (minimum, maximum, threshold) that forms check in place.

A single source for "New profile" and "Edit profile": the domain enforces them anyway (a test
checks that the ranges match); here we only say which rule is violated, in categories.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    threshold_for_sport,
)

# Control ranges; the domain enforces them (a test checks that they match).
MIN_ALLOWED_BPM = 20
MAX_ALLOWED_BPM = 250
MIN_THRESHOLD_BPM = 40


class HeartRateProblem(Enum):
    """Which rule on the three HR values is violated."""

    MIN_NOT_BELOW_MAX = "min_not_below_max"
    THRESHOLD_NOT_ABOVE_MIN = "threshold_not_above_min"
    THRESHOLD_ABOVE_MAX = "threshold_above_max"
    NOT_A_THRESHOLD = "not_a_threshold"  # an optional threshold typed as something else


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


def read_optional_threshold(text: str) -> ThresholdHeartRate | HeartRateProblem | None:
    """An optional threshold typed as text: `None` if empty (the default applies), the threshold
    if it is a whole number of bpm in the threshold range, `NOT_A_THRESHOLD` otherwise."""
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return ThresholdHeartRate(int(stripped))
    except ValueError:
        return HeartRateProblem.NOT_A_THRESHOLD


def optional_threshold_problems(
    min_bpm: int, max_bpm: int, threshold_bpm: int
) -> tuple[HeartRateProblem, ...]:
    """Rules an optional threshold breaks against the resting and maximum rates (the order of
    resting and maximum is reported on their own fields)."""
    return tuple(
        kind
        for kind in heart_rate_problems(min_bpm, max_bpm, threshold_bpm)
        if kind is not HeartRateProblem.MIN_NOT_BELOW_MAX
    )


def optional_threshold_default(min_bpm: int, max_bpm: int, threshold_bpm: int, sport: Sport) -> int:
    """What an optional threshold is when left empty, for the rates shown (the domain decides:
    cycling is running minus 4 kept above the resting rate, other activities the running one).
    Rates that do not fit together still give a number to show; the form reports them apart."""
    try:
        shown = Physiology(
            HeartRateLimits(min_bpm, max_bpm), ThresholdHeartRate(threshold_bpm), Sex.MALE
        )
    except ValueError:
        shown = Physiology(
            HeartRateLimits(MIN_ALLOWED_BPM, MAX_ALLOWED_BPM),
            ThresholdHeartRate(threshold_bpm),
            Sex.MALE,
        )
    return threshold_for_sport(shown, sport)


def bike_default_placeholder(bpm: int) -> str:
    """Placeholder of the empty cycling threshold: what the default is for the rates shown."""
    return QCoreApplication.translate("HeartRate", "default: running - 4 ({value} bpm)").format(
        value=bpm
    )


def other_default_placeholder(bpm: int) -> str:
    """Placeholder of the empty other-activities threshold."""
    return QCoreApplication.translate("HeartRate", "default: running ({value} bpm)").format(
        value=bpm
    )


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
        HeartRateProblem.NOT_A_THRESHOLD: QCoreApplication.translate(
            "HeartRate",
            "Enter a whole number of beats per minute between {low} and {high}, or leave it empty.",
        ).format(low=MIN_THRESHOLD_BPM, high=MAX_ALLOWED_BPM),
    }
    return texts[problem]
