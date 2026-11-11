# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A missed CTL target says why: the weekly hours (and how many would do) or the ramp."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta

import pytest

from ponos_coach.planning.domain import (
    CtlTargetNotReached,
    LimitedByHours,
    LimitedByRamp,
    Plan,
    PlanInputs,
    build_plan,
    defaults,
)
from ponos_coach.planning.plan_codec import decode_plan, digest
from tests.planning.builders import TODAY, availability, fitness_goal, inputs

_STEP = defaults.HOURS_SEARCH_STEP_MIN


def _scenario(minutes: int, target_ctl: float = 45.0, weeks: int = 16) -> PlanInputs:
    """Four sessions a week and a fitness goal, from a low CTL."""
    return inputs(
        [],
        [fitness_goal("f", TODAY + timedelta(weeks=weeks), target_ctl)],
        ctl=25.0,
        atl=25.0,
        avail=availability([1, 3, 5, 6], 4, 6, minutes),
    )


def _plan(source: PlanInputs) -> Plan:
    result = build_plan(source, TODAY)
    assert isinstance(result, Plan)
    return result


def _missed(plan: Plan) -> list[CtlTargetNotReached]:
    return [w for w in plan.warnings if isinstance(w, CtlTargetNotReached)]


def _with_minutes(source: PlanInputs, minutes: int) -> PlanInputs:
    return replace(source, availability=replace(source.availability, max_week_minutes=minutes))


@pytest.mark.parametrize("minutes", [240, 300, 360])
def test_few_hours_say_how_many_minutes_would_reach_the_target(minutes: int) -> None:
    source = _scenario(minutes)
    (warning,) = _missed(_plan(source))
    assert isinstance(warning.limit, LimitedByHours)
    needed = warning.limit.minutes_needed
    assert needed > minutes
    assert needed % _STEP == 0
    assert _missed(_plan(_with_minutes(source, needed))) == []  # enough
    assert _missed(_plan(_with_minutes(source, needed - _STEP))) != []  # and the least on the grid


def test_a_target_too_near_in_time_is_limited_by_the_ramp_and_suggests_no_minutes() -> None:
    (warning,) = _missed(_plan(_scenario(300, target_ctl=60.0, weeks=3)))
    assert warning.limit == LimitedByRamp()


def test_no_amount_of_hours_up_to_the_search_maximum_is_the_ramp() -> None:
    source = _scenario(defaults.HOURS_SEARCH_MAX_MIN, target_ctl=60.0, weeks=3)
    (warning,) = _missed(_plan(source))
    assert warning.limit == LimitedByRamp()


def test_with_the_hours_set_the_plan_is_the_one_without_the_analysis() -> None:
    # The analysis only adds the cause: days, weeks and projection do not move.
    source = _scenario(300)
    plan = _plan(source)
    wider = _plan(_with_minutes(source, 600))
    assert _missed(wider) == []
    assert plan.days != wider.days  # the hours do change the plan...
    again = _plan(source)
    assert plan == again and plan.to_json() == again.to_json()  # ...but never from run to run


def test_a_missed_target_with_its_cause_survives_the_saved_text() -> None:
    plan = _plan(_scenario(300))
    text = plan.to_json()
    read = decode_plan(text, digest(text))
    assert read.to_json() == text
    assert [w.limit for w in _missed(read)] == [w.limit for w in _missed(plan)]
    assert isinstance(_missed(read)[0].limit, LimitedByHours)


def test_a_plan_saved_before_the_cause_existed_reads_as_ramp() -> None:
    plan = _plan(_scenario(300, target_ctl=60.0, weeks=3))
    tree = json.loads(plan.to_json())
    for warning in tree["warnings"]:
        warning.pop("limit")
    text = json.dumps(tree, sort_keys=True, separators=(",", ":"))
    read = decode_plan(text, digest(text))
    assert all(w.limit == LimitedByRamp() for w in _missed(read))


def test_the_minutes_needed_are_more_than_the_ones_set() -> None:
    with pytest.raises(ValueError, match="more than"):
        LimitedByHours(300, 300)
