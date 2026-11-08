# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-PERF for phase 5: the plan of a whole year, the rebuild after a synchronisation on a profile
with many activities, and the guidance of a day ahead.

Times are in milliseconds from a monotonic clock, as a distribution (best, median, worst of a few
runs), never a mean. The budgets are generous multiples of the measured times (the plan of 52
weeks takes about 10 ms; the budget is 1 s): they catch an order of magnitude, not noise.
"""

from __future__ import annotations

import statistics
import sys
from collections.abc import Callable, Iterator
from datetime import date, timedelta
from time import perf_counter

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal
from ponos_coach.nutrition.domain import GoalPeriod, WeightLossGoal
from ponos_coach.planning.domain import Plan, build_plan
from ponos_coach.planning.plan_codec import decode_plan, digest
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from tests.collaudo.fase5_support import FIRST_DAY, TODAY, World, make_world, race
from tests.planning.builders import BIG_WEEK, inputs
from tests.planning.builders import race as pure_race

PLAN_BUDGET_MS = 1_000.0  # PLAN.md and the skill: a plan of 52 weeks in less than a second
REBUILD_BUDGET_MS = 2_000.0  # overview with a warm load cache + plan + write
FUTURE_DAY_BUDGET_MS = 250.0
RUNS = 7


def timed(action: Callable[[], object], runs: int = RUNS) -> list[float]:
    """The milliseconds of each of `runs` calls."""
    times = []
    for _ in range(runs):
        started = perf_counter()
        action()
        times.append((perf_counter() - started) * 1000.0)
    return times


def describe(times: list[float]) -> str:
    median = statistics.median(times)
    return f"best {min(times):.1f} ms, median {median:.1f} ms, worst {max(times):.1f} ms"


# --- The plan alone -------------------------------------------------------------------------------
def year_inputs() -> object:
    today = date(2026, 3, 2)
    goals = [
        pure_race("a1", today + timedelta(days=364), Priority.A, 42_195),  # the edge of the horizon
        pure_race("b1", today + timedelta(days=200), Priority.B, 21_097),
        pure_race("c1", today + timedelta(days=100), Priority.C, 10_000),
        pure_race("a0", today + timedelta(days=150), Priority.A, 10_000),
    ]
    return inputs(goals, avail=BIG_WEEK, ctl=60.0, atl=70.0)


def test_t_perf_a_plan_of_52_weeks_with_four_goals_takes_far_less_than_a_second() -> None:
    base = year_inputs()
    plan = build_plan(base, date(2026, 3, 2))  # type: ignore[arg-type]
    assert isinstance(plan, Plan)
    assert len(plan.days) == 364

    times = timed(lambda: build_plan(base, date(2026, 3, 2)))  # type: ignore[arg-type]
    sys.stdout.write(f"[T-PERF] plan 52 weeks, 4 goals: {describe(times)}\n")

    assert statistics.median(times) < PLAN_BUDGET_MS / 5
    assert max(times) < PLAN_BUDGET_MS


def test_t_perf_the_plan_is_written_and_read_back_quickly() -> None:
    plan = build_plan(year_inputs(), date(2026, 3, 2))  # type: ignore[arg-type]
    assert isinstance(plan, Plan)

    encode = timed(plan.to_json)
    text = plan.to_json()
    decode = timed(lambda: decode_plan(text, digest(text)))
    sys.stdout.write(
        f"[T-PERF] plan JSON {len(text) / 1024:.0f} KiB: "
        f"write {describe(encode)}; read {describe(decode)}\n"
    )

    assert len(text) < 2 * 1024 * 1024  # a year well under the 16 MiB cap
    assert max(encode) < PLAN_BUDGET_MS and max(decode) < PLAN_BUDGET_MS


# --- On a profile with many activities ------------------------------------------------------------
@pytest.fixture(scope="module")
def busy(tmp_path_factory: pytest.TempPathFactory) -> Iterator[World]:
    base = tmp_path_factory.mktemp("f5-perf") / "p"
    base.mkdir()
    opened = make_world(base)
    # 150 runs of 20 minutes over the last 300 days (one every two days)
    day = TODAY - timedelta(days=300)
    while day < TODAY:
        opened.import_run(day, minutes=20, bpm=148)
        day += timedelta(days=2)
    opened.profile.session.goal_periods.add(GoalPeriod(FIRST_DAY, None, WeightLossGoal(500)))
    opened.add_goal(
        RaceGoal(GoalId("a"), TODAY + timedelta(days=300), Priority.A, Sport.RUNNING, 42_195, None)
    )
    opened.add_goal(race("b", TODAY + timedelta(days=150), Priority.B, 21_097))
    opened.overview()  # the first overview computes and caches every load
    yield opened
    opened.close()


def test_t_perf_the_rebuild_after_a_synchronisation_with_a_warm_cache(busy: World) -> None:
    assert len(busy.profile.records()) >= 140

    def rebuild() -> None:
        result = busy.rebuild(busy.today)
        assert isinstance(result, PlanBuilt)

    rebuild()
    times = timed(rebuild)
    sys.stdout.write(
        f"[T-PERF] rebuild with {len(busy.profile.records())} activities, "
        f"warm cache: {describe(times)}\n"
    )

    assert max(times) < REBUILD_BUDGET_MS


def test_t_perf_the_guidance_of_a_day_ahead_does_not_read_every_activity(busy: World) -> None:
    busy.built()
    target = TODAY + timedelta(days=40)
    busy.report(target)

    times = timed(lambda: busy.report(target), 15)
    sys.stdout.write(
        f"[T-PERF] nutrition of a day ahead, {len(busy.profile.records())} activities: "
        f"{describe(times)}\n"
    )

    assert statistics.median(times) < FUTURE_DAY_BUDGET_MS / 2
    assert max(times) < FUTURE_DAY_BUDGET_MS


def test_t_perf_the_guidance_of_a_whole_month_ahead(busy: World) -> None:
    busy.built()
    days = [TODAY + timedelta(days=n) for n in range(1, 31)]

    started = perf_counter()
    for day in days:
        busy.report(day)
    total = (perf_counter() - started) * 1000.0
    sys.stdout.write(
        f"[T-PERF] nutrition of 30 days ahead: {total:.0f} ms ({total / 30:.1f} ms a day)\n"
    )

    assert total < 30 * FUTURE_DAY_BUDGET_MS


def test_t_perf_the_adherence_of_a_year_of_history(busy: World) -> None:
    busy.built()
    busy.today = TODAY + timedelta(days=30)
    busy.built()  # freezes 30 days
    times = timed(lambda: busy.adherence(TODAY - timedelta(days=300), busy.today), 5)
    sys.stdout.write(f"[T-PERF] adherence over a year: {describe(times)}\n")
    busy.today = TODAY

    assert max(times) < REBUILD_BUDGET_MS
