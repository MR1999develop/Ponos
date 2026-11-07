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

persons = st.builds(
    Person,
    sex=st.sampled_from(Sex),
    age_years=st.integers(5, 100),
    height_cm=st.integers(100, 250),
    weight_kg=st.integers(200, 3000).map(lambda tenths: tenths / 10.0),
    activity_level=st.none() | st.sampled_from(ActivityLevel),
)

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
)
