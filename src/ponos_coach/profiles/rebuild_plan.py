# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases that tie the plan to the rest of the profile.

- `RebuildPlan`: gathers what the plan engine needs (the PMC of what was really done, the goals, the
  availability, the session types, the day's physiology, the sport weights, the last threshold
  pace, the retest status, the open injuries and illnesses), calls `build_plan` and saves the
  result if it differs from the last one.
- `PlanAdherence`: planned against done for a range of days, using what was planned *at the time*.

The past stays history. A plan only covers the days after the day it was generated, so when a new
plan replaces one, the days of the old plan that have become past (up to today) are frozen first,
each with the date of the plan it came from, and a frozen day never changes. A calendar of the
past then shows what was really planned on each day, not what today's plan would have said.

A refusal (no goals, a goal in the past...) is a state like any plan: it is saved too, so the
interface can show why there is no plan. If the physiological parameters are missing nothing is
saved. Health-related data: never in logs or error messages.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.assessment.domain import retest_status
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.goals.domain import FitnessGoal, RaceGoal, WeeklyAvailability
from ponos_coach.goals.ports import AvailabilityStore, GoalStore, SessionChoicesStore
from ponos_coach.injury.domain import plan_injuries, recently_returned
from ponos_coach.injury.ports import InjuryStore
from ponos_coach.load.domain import (
    MissingPhysiologyError,
    compute_pmc,
    effective_weights,
    physiology_for_day,
)
from ponos_coach.load.ports import PhysiologyStore, SportWeightStore
from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.planning.domain import (
    DayAdherence,
    FitnessState,
    Plan,
    PlanInputs,
    PlanRefusal,
    build_plan,
    compare_with_actual,
)
from ponos_coach.planning.ports import FrozenDay, PlanStore, StoredPlan
from ponos_coach.profiles.use_cases import ProfileSession

_NO_AVAILABILITY = WeeklyAvailability((0, 0, 0, 0, 0, 0, 0), 6, 0)


@dataclass(frozen=True, slots=True)
class PlanSources:
    """The stores of the open profile that the plan reads and writes."""

    activities: ActivityIndex
    goals: GoalStore
    availability: AvailabilityStore
    choices: SessionChoicesStore
    physiology: PhysiologyStore
    sport_weights: SportWeightStore
    threshold_tests: ThresholdTestStore
    plans: PlanStore
    injuries: InjuryStore


def plan_sources_of(session: ProfileSession) -> PlanSources:
    """The plan's stores of an open profile."""
    return PlanSources(
        session.activities,
        session.goals,
        session.availability,
        session.session_choices,
        session.physiology,
        session.sport_weights,
        session.threshold_tests,
        session.plans,
        session.injuries,
    )


# --- What a rebuild gives back -------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class PlanBuilt:
    """A plan; `saved` is False when it is the same as the last one (nothing was written)."""

    plan: Plan
    saved: bool


@dataclass(frozen=True, slots=True)
class PlanRefused:
    """No plan, and why; `saved` is False when the last result was already this refusal."""

    refusal: PlanRefusal
    saved: bool


class NotBuiltReason(Enum):
    """Why the plan engine was not even asked. The value is a stable identifier."""

    MISSING_PHYSIOLOGY = "missing_physiology"  # no heart-rate parameters yet


@dataclass(frozen=True, slots=True)
class PlanNotBuilt:
    """The plan could not be attempted; nothing was saved and the last plan stays."""

    reason: NotBuiltReason


type RebuildResult = PlanBuilt | PlanRefused | PlanNotBuilt


# --- Shared steps --------------------------------------------------------------------------------
def _daily_loads(overview: ProfileOverview) -> dict[date, float]:
    """The load actually done on each local day (sum over the activities)."""
    daily: dict[date, float] = defaultdict(float)
    for activity in overview.activities:
        daily[activity.local_day] += activity.load.load
    return dict(daily)


def _fitness_on(daily: dict[date, float], today: date) -> FitnessState:
    """CTL and ATL at the end of `today`: the PMC of the overview (same function, same days)."""
    if not daily or min(daily) > today:
        return FitnessState(0.0, 0.0)
    pmc = compute_pmc(daily, min(daily), today)
    return FitnessState(float(pmc.ctl[-1]), float(pmc.atl[-1]))


def _same_result(saved: Plan | PlanRefusal, new: Plan | PlanRefusal) -> bool:
    """Two results are the same if their canonical forms are (a plan is saved rounded)."""
    if isinstance(saved, Plan) and isinstance(new, Plan):
        return saved.to_json() == new.to_json()
    return isinstance(saved, PlanRefusal) and saved == new


class RebuildPlan:
    """Rebuilds the plan from today and saves it if it changed.

    Volatile dependencies injected: the stores and `overview`, which gives the loads of what was
    done (the overview use case: the load cache makes it cheap, or an overview just computed).
    """

    def __init__(self, sources: PlanSources, overview: Callable[[], ProfileOverview]) -> None:
        self._src = sources
        self._overview = overview

    def __call__(self, today: date) -> RebuildResult:
        """The plan from tomorrow on (or the refusal), saved if different from the last result.

        `today` is the profile's local date. The days of the old plan up to today are frozen as
        part of the same transaction that replaces it; if nothing changed, nothing is written.

        Raises:
            OverviewCancelledError: the overview was cancelled; nothing is saved.
            LoadStoreError, AssessmentStoreError, GoalStoreError, InjuryStoreError,
                PlanStoreError: a store could not be read or written; nothing is saved.
        """
        src = self._src
        try:  # first: without parameters the overview itself cannot be computed
            physiology = physiology_for_day(src.physiology.list_entries(), today)
        except MissingPhysiologyError:
            return PlanNotBuilt(NotBuiltReason.MISSING_PHYSIOLOGY)
        fitness = _fitness_on(_daily_loads(self._overview()), today)
        goals = src.goals.list_goals()
        saved_availability = src.availability.get()
        tests = [t for t in src.threshold_tests.list_tests() if t.test_day <= today]
        problems = src.injuries.list_problems()
        inputs = PlanInputs(
            races=tuple(g for g in goals if isinstance(g, RaceGoal)),
            fitness_goals=tuple(g for g in goals if isinstance(g, FitnessGoal)),
            availability=_NO_AVAILABILITY if saved_availability is None else saved_availability,
            choices=src.choices.get(),
            physiology=physiology,
            fitness=fitness,
            weights=effective_weights(src.sport_weights.load()),
            threshold_pace_s_per_km=next(
                (t.pace_s_per_km for t in reversed(tests) if t.pace_s_per_km is not None), None
            ),
            retest=retest_status((t.test_day for t in tests), today),
            injuries=plan_injuries(problems),
            after_injury=recently_returned(problems, today),
        )
        built = build_plan(inputs, today)
        saved = self._save(built, today)
        if isinstance(built, Plan):
            return PlanBuilt(built, saved)
        return PlanRefused(built, saved)

    def _save(self, result: Plan | PlanRefusal, today: date) -> bool:
        """Replaces the saved result with this one, freezing the past days of the old plan."""
        plans = self._src.plans
        with self._src.activities.transaction():
            stored = plans.current()
            if isinstance(stored, StoredPlan):
                if _same_result(stored.state, result):
                    return False
                if isinstance(stored.state, Plan):
                    plans.freeze(
                        [
                            FrozenDay(stored.generated_on, day)
                            for day in stored.state.days
                            if stored.generated_on < day.day <= today
                        ]
                    )
            plans.replace_current(result, today)
        return True


class PlanAdherence:
    """Planned against done, day by day."""

    def __init__(
        self,
        plans: PlanStore,
        overview: Callable[[], ProfileOverview],
        today: Callable[[], date],
    ) -> None:
        self._plans = plans
        self._overview = overview
        self._today = today

    def __call__(self, first: date, last: date) -> tuple[DayAdherence, ...]:
        """For each planned day from `first` to `last` (not beyond today) the load planned and the
        load done, by ascending day. A day nobody planned is not there, even if something was
        done. Past days use what was planned at the time; days not yet frozen use the current plan.

        Raises:
            OverviewCancelledError: the overview was cancelled.
            LoadStoreError, PlanStoreError: a store could not be read.
        """
        last = min(last, self._today())
        if last < first:
            return ()
        planned = {f.day.day: f.day for f in self._plans.past_days(first, last)}
        stored = self._plans.current()
        current = stored.state if isinstance(stored, StoredPlan) else None
        if isinstance(current, Plan):
            for day in current.days:
                if first <= day.day <= last:
                    planned.setdefault(day.day, day)
        actual = _daily_loads(self._overview())
        days = tuple(planned[d] for d in sorted(planned))
        if isinstance(current, Plan):
            return compare_with_actual(replace(current, days=days), actual, last)
        return tuple(DayAdherence(d.day, d.load, actual.get(d.day, 0.0)) for d in days)
