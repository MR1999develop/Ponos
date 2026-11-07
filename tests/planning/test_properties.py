# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-02: the constraints hold for any inputs, not only the scenarios we thought of."""

from __future__ import annotations

from datetime import timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
    WeeklyAvailability,
)
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.planning.domain import (
    FitnessState,
    Phase,
    Plan,
    PlanInputs,
    PlanRefusal,
    RefusalReason,
    WeekKind,
    build_plan,
)
from tests.planning.builders import TODAY
from tests.planning.invariants import violations

_day_minutes = st.sampled_from([0, 0, 20, 30, 45, 60, 90, 120, 180])
_availability = st.builds(
    WeeklyAvailability,
    st.tuples(*[_day_minutes] * 7),
    st.integers(0, 6),
    st.sampled_from([0, 60, 180, 300, 420, 600, 900, 1500]),
)
_types = st.sets(st.sampled_from(list(SessionType)), min_size=1)
_priorities = st.sampled_from(list(Priority))
_distances = st.sampled_from([1_500, 5_000, 10_000, 21_097, 42_195, 80_000])


@st.composite
def _inputs(draw: st.DrawFn) -> PlanInputs:
    offsets = draw(st.lists(st.integers(-2, 420), min_size=0, max_size=5, unique=True))
    races = []
    for index, offset in enumerate(offsets):
        target = draw(st.one_of(st.none(), st.integers(600, 20_000)))
        races.append(
            RaceGoal(
                GoalId(f"r{index}"),
                TODAY + timedelta(days=offset),
                draw(_priorities),
                Sport.RUNNING,
                draw(_distances),
                target,
            )
        )
    fitness = [
        FitnessGoal(
            GoalId(f"f{i}"),
            TODAY + timedelta(days=draw(st.integers(1, 420))),
            draw(st.floats(0, 120)),
        )
        for i in range(draw(st.integers(0, 2)))
    ]
    ctl = draw(st.floats(0, 130))
    atl = draw(st.floats(0, 180))
    pace = draw(st.one_of(st.none(), st.integers(180, 600)))
    return PlanInputs(
        tuple(races),
        tuple(fitness),
        draw(_availability),
        SessionChoices.of({Sport.RUNNING: draw(_types)}),
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE),
        FitnessState(ctl, atl),
        threshold_pace_s_per_km=pace,
        after_injury=draw(st.booleans()),
    )


@given(_inputs())
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_every_plan_respects_the_constraints(inputs: PlanInputs) -> None:
    result = build_plan(inputs, TODAY)
    if isinstance(result, PlanRefusal):
        assert result.reason in set(RefusalReason)
        return
    assert violations(result) == []
    assert_base_weeks_are_80_20(result)


def assert_base_weeks_are_80_20(plan: Plan) -> None:
    """In base and maintenance weeks at least 80% of the training time is in Z1-Z2."""
    for week in plan.weeks:
        days = [
            d for d in plan.days if week.week_start <= d.day < week.week_start + timedelta(days=7)
        ]
        if (
            not all(d.phase in (Phase.BASE, Phase.MAINTENANCE) for d in days)
            or week.kind is WeekKind.UNLOAD
        ):
            continue
        low = total = 0
        for day in days:
            if day.session is None:
                continue
            for block in day.session.blocks:
                minutes = block.minutes * block.repeats
                total += minutes
                low += minutes if block.zone <= 2 else 0
        assert low * 100 >= 80 * total, (week.week_start, low, total)
