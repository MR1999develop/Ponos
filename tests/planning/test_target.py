# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PIANO-05: the CTL a race asks for and the class of a target time, at the boundaries."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.planning.domain import (
    Plan,
    TargetClass,
    TargetTimeUnreachable,
    build_plan,
    classify_target,
)
from ponos_coach.planning.domain.target import (
    best_time_by_race_s,
    predicted_time_today_s,
    recovery_days_for,
    taper_days_for,
    target_ctl_for_distance,
)
from tests.planning.builders import TODAY, inputs, race


@pytest.mark.parametrize(
    ("distance", "ctl"),
    [
        (1_000, 40.0),
        (5_000, 40.0),
        (7_500, 42.5),
        (10_000, 45.0),
        (21_097, 55.0),
        (42_195, 70.0),
        (46_098, 75.0),
        (50_000, 80.0),
        (100_000, 80.0),
    ],
)
def test_ctl_target_by_distance(distance: int, ctl: float) -> None:
    assert target_ctl_for_distance(distance) == pytest.approx(ctl, abs=0.01)


@pytest.mark.parametrize(
    ("distance", "taper", "recovery"),
    [
        (5_000, 7, 3),
        (10_000, 7, 3),
        (10_001, 10, 5),
        (21_097, 10, 5),
        (21_098, 14, 7),
        (42_195, 14, 7),
    ],
)
def test_taper_and_recovery_by_distance(distance: int, taper: int, recovery: int) -> None:
    assert taper_days_for(distance) == taper
    assert recovery_days_for(distance) == recovery


def test_riegel_from_the_hour_at_threshold() -> None:
    # 5:00/km for an hour is 12 km: that distance takes exactly an hour.
    assert predicted_time_today_s(300, 12_000) == pytest.approx(3600.0)
    assert predicted_time_today_s(300, 21_097) == pytest.approx(3600 * (21_097 / 12_000) ** 1.06)


@pytest.mark.parametrize(
    ("days", "factor"),
    [(0, 1.0), (7, 0.995), (14, 0.99), (112, 0.92), (113, 0.92), (400, 0.92), (-5, 1.0)],
)
def test_improvement_is_half_a_percent_a_week_up_to_eight(days: int, factor: float) -> None:
    assert best_time_by_race_s(300, 12_000, days) == pytest.approx(3600 * factor)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (1001, TargetClass.REALISTIC),
        (1000, TargetClass.REALISTIC),  # not faster than the best time
        (999, TargetClass.AMBITIOUS),
        (970, TargetClass.AMBITIOUS),  # exactly 3% faster
        (969, TargetClass.UNREACHABLE),
        (300, TargetClass.UNREACHABLE),
    ],
)
def test_classes_at_the_boundaries(target: int, expected: TargetClass) -> None:
    assert classify_target(target, 1000.0) is expected


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        (3582, TargetClass.REALISTIC),
        (3581, TargetClass.AMBITIOUS),
        (3475, TargetClass.AMBITIOUS),
        (3474, TargetClass.UNREACHABLE),
    ],
)
def test_classes_through_the_plan(target: int, expected: TargetClass) -> None:
    # 12 km in a week from today, threshold pace 5:00/km: best time 3600 * 0.995 = 3582 s
    goal = race("r", TODAY + timedelta(days=7), distance_m=12_000, target_time_s=target)
    plan = build_plan(inputs([goal], pace=300), TODAY)
    assert isinstance(plan, Plan)
    assert plan.targets[0].classification is expected
    assert plan.targets[0].best_time_s == 3582
    has_warning = any(isinstance(w, TargetTimeUnreachable) for w in plan.warnings)
    assert has_warning == (expected is TargetClass.UNREACHABLE)


def test_target_without_threshold_pace_is_not_estimable() -> None:
    goal = race("r", TODAY + timedelta(days=30), target_time_s=3000)
    plan = build_plan(inputs([goal], pace=None), TODAY)
    assert isinstance(plan, Plan)
    assert plan.targets[0].classification is TargetClass.NOT_ESTIMABLE
    assert plan.targets[0].best_time_s is None


def test_target_on_a_non_running_race_is_not_estimable() -> None:
    cycling = race("r", TODAY + timedelta(days=30), target_time_s=3000, sport=Sport.CYCLING)
    plan = build_plan(inputs([cycling], pace=300, choices_for=Sport.CYCLING), TODAY)
    assert isinstance(plan, Plan)
    assert plan.targets[0].classification is TargetClass.NOT_ESTIMABLE


def test_races_without_a_target_time_have_no_verdict() -> None:
    plan = build_plan(inputs([race("r", TODAY + timedelta(days=30))]), TODAY)
    assert isinstance(plan, Plan)
    assert plan.targets == ()
