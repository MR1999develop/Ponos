# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`PlannedDay`: what the plan has for one day, as the calendar shows it (a frozen past day wins
over the current plan). Made-up plan."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import date, timedelta
from typing import cast

from ponos_coach.planning.domain import Plan, PlanRefusal, build_plan
from ponos_coach.planning.ports import FrozenDay, PlanStore, StoredPlan, UnreadablePlan
from ponos_coach.planning.use_cases import PlannedDay
from tests.planning.builders import BIG_WEEK, TODAY, inputs, race


class _Store:
    """Only the reads `PlannedDay` makes."""

    def __init__(
        self, current: StoredPlan | UnreadablePlan | None, frozen: Sequence[FrozenDay] = ()
    ) -> None:
        self._current = current
        self._frozen = {item.day.day: item for item in frozen}

    def current(self) -> StoredPlan | UnreadablePlan | None:
        return self._current

    def past_days(self, first: date, last: date) -> Sequence[FrozenDay]:
        return [item for day, item in sorted(self._frozen.items()) if first <= day <= last]


def planned(store: _Store) -> PlannedDay:
    """The use case on the reads-only store (it calls nothing else of `PlanStore`)."""
    return PlannedDay(cast(PlanStore, store))


def a_plan() -> Plan:
    built = build_plan(inputs([race("a", TODAY + timedelta(weeks=14))], avail=BIG_WEEK), TODAY)
    assert isinstance(built, Plan)
    return built


def test_a_day_of_the_current_plan_comes_from_it() -> None:
    plan = a_plan()
    target = plan.days[3]

    assert planned(_Store(StoredPlan(plan, TODAY)))(target.day) == target


def test_a_frozen_past_day_wins_over_the_current_plan() -> None:
    plan = a_plan()
    target = plan.days[3]
    then = dataclasses.replace(target, session=None)
    store = _Store(StoredPlan(plan, TODAY), [FrozenDay(target.day - timedelta(days=7), then)])

    assert planned(store)(target.day) == then


def test_no_plan_a_refusal_or_a_day_outside_the_plan_give_none() -> None:
    plan = a_plan()
    outside = plan.days[-1].day + timedelta(days=1)
    refusal = build_plan(inputs([]), TODAY)

    assert planned(_Store(None))(TODAY) is None
    assert planned(_Store(StoredPlan(plan, TODAY)))(outside) is None
    if isinstance(refusal, PlanRefusal):
        assert planned(_Store(StoredPlan(refusal, TODAY)))(TODAY) is None
