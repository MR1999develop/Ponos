# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Goal types are valid by construction: out-of-range values cannot be built."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
)

DAY = date(2026, 6, 1)
SEVEN_DAYS_S = 7 * 24 * 3600


@pytest.mark.parametrize("value", ["", "a b", "x" * 65, "é", "a/b"])
def test_goal_id_rejects_odd_text(value: str) -> None:
    with pytest.raises(ValueError, match="goal id"):
        GoalId(value)


@pytest.mark.parametrize("value", ["a", "race_1", "A-B", "x" * 64])
def test_goal_id_accepts_plain_text(value: str) -> None:
    assert GoalId(value).value == value


@pytest.mark.parametrize(
    ("distance", "target"),
    [(399, None), (500_001, None), (10_000, 59), (10_000, SEVEN_DAYS_S + 1)],
)
def test_race_rejects_values_out_of_range(distance: int, target: int | None) -> None:
    with pytest.raises(ValueError, match=r"distance|target"):
        RaceGoal(GoalId("r"), DAY, Priority.A, Sport.RUNNING, distance, target)


@pytest.mark.parametrize(
    ("distance", "target"),
    [(400, None), (500_000, None), (10_000, 60), (10_000, SEVEN_DAYS_S)],
)
def test_race_accepts_the_limits(distance: int, target: int | None) -> None:
    race = RaceGoal(GoalId("r"), DAY, Priority.B, Sport.RUNNING, distance, target)
    assert race.distance_m == distance


@pytest.mark.parametrize("ctl", [-0.1, 200.1, float("nan"), float("inf")])
def test_fitness_goal_rejects_ctl_out_of_range(ctl: float) -> None:
    with pytest.raises(ValueError, match="CTL"):
        FitnessGoal(GoalId("f"), DAY, ctl)


@pytest.mark.parametrize("ctl", [0.0, 100.0, 200.0])
def test_fitness_goal_accepts_the_limits(ctl: float) -> None:
    assert FitnessGoal(GoalId("f"), DAY, ctl).ctl == ctl


def test_goal_dates_before_1900_are_rejected() -> None:
    with pytest.raises(ValueError, match="1900"):
        FitnessGoal(GoalId("f"), date(1899, 12, 31), 10.0)


def test_session_choices_have_one_canonical_form() -> None:
    a = SessionChoices.of({Sport.CYCLING: [SessionType.EASY], Sport.RUNNING: [SessionType.LONG]})
    b = SessionChoices.of({Sport.RUNNING: {SessionType.LONG}, Sport.CYCLING: (SessionType.EASY,)})
    assert a == b
    assert a.for_sport(Sport.RUNNING) == {SessionType.LONG}
    assert a.for_sport(Sport.HIKING) == frozenset()


def test_session_choices_drop_sports_without_types() -> None:
    assert SessionChoices.of({Sport.RUNNING: []}).by_sport == ()


def test_session_choices_reject_a_hand_made_unsorted_form() -> None:
    easy = frozenset({SessionType.EASY})
    with pytest.raises(ValueError, match="sorted"):
        SessionChoices(((Sport.RUNNING, easy), (Sport.CYCLING, easy)))
