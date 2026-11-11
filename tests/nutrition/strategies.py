# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Hypothesis strategies for the nutrition tests: persons, goals, sessions, whole days."""

from __future__ import annotations

from datetime import time

from hypothesis import strategies as st

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    DayInput,
    MaintenanceGoal,
    MealTimes,
    NutritionGoal,
    PerformanceGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
)


@st.composite
def persons_fn(draw: st.DrawFn) -> Person:
    """A person, with a measured lean mass (plausible for the weight) about half of the time."""
    weight_kg = draw(st.integers(200, 3000).map(lambda tenths: tenths / 10.0))
    lean_tenths = draw(st.none() | st.integers(40, 95))  # percent of the weight
    return Person(
        sex=draw(st.sampled_from(Sex)),
        age_years=draw(st.integers(5, 100)),
        height_cm=draw(st.integers(100, 250)),
        weight_kg=weight_kg,
        activity_level=draw(st.none() | st.sampled_from(ActivityLevel)),
        lean_mass_kg=None if lean_tenths is None else round(weight_kg * lean_tenths / 100, 1),
    )


persons = persons_fn()

goals: st.SearchStrategy[NutritionGoal] = st.one_of(
    st.just(MaintenanceGoal()),
    st.just(PerformanceGoal()),
    st.builds(WeightLossGoal, st.integers(100, 1000)),
)

sessions = st.builds(
    TrainingSession,
    start_minute=st.integers(0, 1439),
    duration_min=st.integers(1, 600).map(float),
    net_kcal=st.integers(0, 4000).map(float),
    load=st.integers(0, 400).map(float),
)


@st.composite
def meal_times(draw: st.DrawFn) -> MealTimes:
    """Three meal times at least two hours apart, in order."""
    breakfast = draw(st.integers(0, 1439 - 240))
    lunch = draw(st.integers(breakfast + 120, 1439 - 120))
    dinner = draw(st.integers(lunch + 120, 1439))
    return MealTimes(
        time(breakfast // 60, breakfast % 60),
        time(lunch // 60, lunch % 60),
        time(dinner // 60, dinner % 60),
    )


days = st.builds(
    DayInput,
    person=persons,
    goal=goals,
    meal_times=meal_times(),
    sessions=st.lists(sessions, max_size=3).map(tuple),
    minutes_to_next_session=st.none() | st.integers(0, 3000).map(float),
    weight_loss_days=st.none() | st.integers(0, 400),
)
