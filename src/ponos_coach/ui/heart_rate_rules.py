# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rules for the HR values (minimum, maximum, LT2, LT1) that forms check in place.

A single source for "New profile" and "Edit profile": the domain enforces them anyway (a test
checks that the ranges match); here we only say which rule is violated, in categories.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    DEFAULT_LT1_PERCENT,
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    default_lt1,
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
    LT1_NOT_ABOVE_MIN = "lt1_not_above_min"
    LT1_NOT_BELOW_LT2 = "lt1_not_below_lt2"


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


def lt1_problems(min_bpm: int, lt1_bpm: int, lt2_bpm: int) -> tuple[HeartRateProblem, ...]:
    """Rules an entered LT1 breaks: it must lie above the resting rate and below the LT2 of its
    group (`min HR < LT1 < LT2`)."""
    found: list[HeartRateProblem] = []
    if lt1_bpm <= min_bpm:
        found.append(HeartRateProblem.LT1_NOT_ABOVE_MIN)
    if lt1_bpm >= lt2_bpm:
        found.append(HeartRateProblem.LT1_NOT_BELOW_LT2)
    return tuple(found)


def optional_threshold_default(min_bpm: int, max_bpm: int, threshold_bpm: int, sport: Sport) -> int:
    """What the other-sports LT2 is when left empty, for the rates shown (the domain decides: the
    running one). Rates that do not fit together still give a number to show; the form reports
    them apart."""
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


def lt1_default(min_bpm: int, lt2_bpm: int) -> int:
    """What an LT1 is when left empty: the domain's default for the LT2 of its group."""
    return default_lt1(lt2_bpm, min_bpm)


def lt1_default_placeholder(bpm: int) -> str:
    """Placeholder of an empty LT1: what the default is for the rates shown."""
    return QCoreApplication.translate(
        "HeartRate", "default: {percent}% of LT2 ({value} bpm)"
    ).format(percent=DEFAULT_LT1_PERCENT, value=bpm)


def other_default_placeholder(bpm: int) -> str:
    """Placeholder of the empty other-sports LT2."""
    return QCoreApplication.translate("HeartRate", "default: running LT2 ({value} bpm)").format(
        value=bpm
    )


def heart_rate_problem_text(problem: HeartRateProblem) -> str:
    """Message under the field: which rule fails and how to fix it."""
    texts = {
        HeartRateProblem.MIN_NOT_BELOW_MAX: QCoreApplication.translate(
            "HeartRate", "The resting heart rate must be lower than the maximum."
        ),
        HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: QCoreApplication.translate(
            "HeartRate", "LT2 must be higher than the resting heart rate."
        ),
        HeartRateProblem.THRESHOLD_ABOVE_MAX: QCoreApplication.translate(
            "HeartRate", "LT2 cannot be higher than the maximum."
        ),
        HeartRateProblem.LT1_NOT_ABOVE_MIN: QCoreApplication.translate(
            "HeartRate", "LT1 must be higher than the resting heart rate."
        ),
        HeartRateProblem.LT1_NOT_BELOW_LT2: QCoreApplication.translate(
            "HeartRate", "LT1 must be lower than LT2."
        ),
        HeartRateProblem.NOT_A_THRESHOLD: QCoreApplication.translate(
            "HeartRate",
            "Enter a whole number of beats per minute between {low} and {high}, or leave it empty.",
        ).format(low=MIN_THRESHOLD_BPM, high=MAX_ALLOWED_BPM),
    }
    return texts[problem]


def lt1_tooltip() -> str:
    """What LT1 is and how to estimate it, in one sentence for people who do not know the term."""
    return QCoreApplication.translate(
        "HeartRate",
        "LT1 (aerobic threshold): the heart rate above which an easy effort stops feeling easy. "
        "Estimate it as the highest heart rate at which you can still talk in full sentences, or "
        "where your heart rate stays steady over a long easy effort (little drift). Left empty, "
        "{percent}% of LT2 is used.",
    ).format(percent=DEFAULT_LT1_PERCENT)


def lt2_tooltip() -> str:
    """What LT2 is and how to estimate it."""
    return QCoreApplication.translate(
        "HeartRate",
        "LT2 (lactate threshold): the highest heart rate you can hold for about an hour of hard, "
        "steady effort. Estimate it with a 30-minute threshold test.",
    )
