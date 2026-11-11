# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pure domain of the goals feature (no I/O, Qt, network or logging)."""

from ponos_coach.goals.domain.availability import WeeklyAvailability
from ponos_coach.goals.domain.goal import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    takes_target_time,
)
from ponos_coach.goals.domain.session_types import (
    HARD_TYPES,
    INTENSE_TYPES,
    SessionChoices,
    SessionType,
)

__all__ = [
    "HARD_TYPES",
    "INTENSE_TYPES",
    "FitnessGoal",
    "GoalId",
    "Priority",
    "RaceGoal",
    "SessionChoices",
    "SessionType",
    "WeeklyAvailability",
    "takes_target_time",
]
