# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A paused deficit (race week, taper) in the pure model: none for any person (T-NUT-08)."""

from __future__ import annotations

from dataclasses import replace

from hypothesis import given

from ponos_coach.nutrition.domain import Advisory, DayInput, WeightLossGoal, plan_day
from tests.nutrition.strategies import days


@given(days)
def test_a_paused_day_never_has_a_deficit_and_the_advisory_is_for_weight_loss_only(
    day: DayInput,
) -> None:
    paused = plan_day(replace(day, deficit_paused=True))

    assert paused.deficit_kcal == 0
    assert paused.target_kcal == paused.expenditure_kcal
    said = Advisory.DEFICIT_PAUSED_FOR_RACE in paused.advisories
    assert said is isinstance(day.goal, WeightLossGoal)


@given(days)
def test_a_day_that_is_not_paused_is_what_it_was(day: DayInput) -> None:
    assert day.deficit_paused is False
    assert Advisory.DEFICIT_PAUSED_FOR_RACE not in plan_day(day).advisories
