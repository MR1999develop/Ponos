# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`RebuildPlan`, `CurrentPlan`, `PastPlan` and `PlanAdherence` over an in-memory profile.

The PMC is the real one: synthetic runs go through the overview use case, with its load cache,
exactly as the application does. T-PIANO-04 end to end (skipped sessions, late activities, the
past staying history), and the plan saved only when it changes.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import Overdue, ThresholdTest
from ponos_coach.goals.domain import GoalId, Priority, RaceGoal, SessionChoices, SessionType
from ponos_coach.goals.use_cases import AddGoal, RemoveGoal, SetAvailability, SetSessionChoices
from ponos_coach.injury.domain import (
    BodyZone,
    Illness,
    IllnessKind,
    Injury,
    InjuryId,
)
from ponos_coach.injury.domain import (
    defaults as injury_defaults,
)
from ponos_coach.injury.use_cases import CloseProblem, OpenInjury, ReportIllness
from ponos_coach.load.domain import SportWeight
from ponos_coach.load.use_cases import ComputeProfileOverview, ProfileOverview, SetSportWeights
from ponos_coach.planning.domain import Plan, PlanHalted, PlanRefusal, RefusalReason, defaults
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.planning.ports import FrozenDay, PlanStore, StoredPlan, UnreadablePlan
from ponos_coach.planning.use_cases import CurrentPlan, PastPlan
from ponos_coach.profiles.rebuild_plan import (
    NotBuiltReason,
    PlanAdherence,
    PlanBuilt,
    PlanNotBuilt,
    PlanRefused,
    PlanSources,
    RebuildPlan,
)
from tests.builders import fit_identity, physiology_entry
from tests.injury.builders import questionnaire as injury_questionnaire
from tests.load.test_profile_overview import ROME, World, decode_fit
from tests.planning.builders import BIG_WEEK

TODAY = date(2026, 3, 2)  # a Monday
RACE_DAY = TODAY + timedelta(weeks=16)
ALL_TYPES = frozenset(SessionType)


class SpyPlans:
    """A plan store that counts the writes and lets everything through."""

    def __init__(self, inner: PlanStore) -> None:
        self._inner = inner
        self.replacements = 0
        self.frozen_calls: list[int] = []

    def current(self) -> StoredPlan | UnreadablePlan | None:
        return self._inner.current()

    def replace_current(self, state: Plan | PlanRefusal, generated_on: date) -> None:
        self.replacements += 1
        self._inner.replace_current(state, generated_on)

    def freeze(self, days: Sequence[FrozenDay]) -> None:
        self.frozen_calls.append(len(days))
        self._inner.freeze(days)

    def past_days(self, first: date, last: date) -> Sequence[FrozenDay]:
        return self._inner.past_days(first, last)


class PlanWorld(World):
    """A profile with parameters, three weeks of runs, an A race in 16 weeks and the use cases."""

    def __init__(
        self, *, with_physiology: bool = True, with_goal: bool = True, with_runs: bool = True
    ) -> None:
        super().__init__()
        self.today = TODAY
        self.spy = SpyPlans(self.profile.plans)
        if with_physiology:
            self.profile.physiology.put(physiology_entry(date(2026, 1, 1), 170))
        self.add_goal = AddGoal(self.profile.goals)
        self.remove_goal = RemoveGoal(self.profile.activities, self.profile.goals)
        SetAvailability(self.profile.activities, self.profile.availability)(BIG_WEEK)
        SetSessionChoices(self.profile.activities, self.profile.session_choices)(
            SessionChoices.of({Sport.RUNNING: ALL_TYPES})
        )
        if with_goal:
            self.add_goal(self.race())
        for number in range(1, 8 if with_runs else 1):  # a run every third day, three weeks
            self.run_on(TODAY - timedelta(days=3 * number), number)
        sources = PlanSources(
            self.profile.activities,
            self.profile.goals,
            self.profile.availability,
            self.profile.session_choices,
            self.profile.physiology,
            self.profile.sport_weights,
            self.profile.threshold_tests,
            self.spy,
            self.profile.injuries,
        )
        self.rebuild = RebuildPlan(sources, self.overview_now)
        self.current = CurrentPlan(self.spy)
        self.past = PastPlan(self.spy)
        self.adherence = PlanAdherence(self.spy, self.overview_now, lambda: self.today)

    @staticmethod
    def race(name: str = "marathon", day: date = RACE_DAY) -> RaceGoal:
        return RaceGoal(GoalId(name), day, Priority.A, Sport.RUNNING, 42_195, None)

    def run_on(self, day: date, serial: int) -> None:
        self.add_run(datetime(day.year, day.month, day.day, 7, 0, tzinfo=UTC), serial)

    def overview_now(self) -> ProfileOverview:
        return ComputeProfileOverview(
            self.profile.activities,
            self.archive,
            self.profile.physiology,
            self.profile.load_cache,
            decode_fit,
            sport_weights=self.profile.sport_weights,
            timezone=ROME,
            today=lambda: self.today,
        ).execute()

    def built(self) -> PlanBuilt:
        result = self.rebuild(self.today)
        assert isinstance(result, PlanBuilt)
        return result

    def stored_plan(self) -> Plan:
        stored = self.current()
        assert stored is not None
        assert isinstance(stored.state, Plan)
        return stored.state


def plan_days_json(plan: Plan, first: int, last: int) -> list[str]:
    """The canonical text of the plan's days `first` to `last` (counting from tomorrow as 1)."""
    return [canonical_json(day) for day in plan.days[first - 1 : last]]


# --- The plan starts from the real PMC ------------------------------------------------------------
def test_the_plan_is_built_from_the_pmc_of_the_overview_and_saved() -> None:
    world = PlanWorld()

    result = world.built()

    pmc = world.overview_now().pmc
    assert pmc.days[-1] == TODAY
    assert result.saved
    assert result.plan.generated_on == TODAY
    assert result.plan.inputs.fitness.ctl == pytest.approx(float(pmc.ctl[-1]))
    assert result.plan.inputs.fitness.atl == pytest.approx(float(pmc.atl[-1]))
    assert result.plan.inputs.fitness.ctl > 0
    stored = world.stored_plan()
    assert stored.to_json() == result.plan.to_json()


def test_the_plan_is_made_of_the_profiles_data() -> None:
    world = PlanWorld()
    SetSportWeights(world.profile.activities, world.profile.sport_weights)(
        {Sport.CYCLING: SportWeight(0.5)}
    )

    plan = world.built().plan

    assert plan.inputs.races == (PlanWorld.race(),)
    assert plan.inputs.availability == BIG_WEEK
    assert plan.inputs.choices == SessionChoices.of({Sport.RUNNING: ALL_TYPES})
    assert plan.inputs.weights[Sport.CYCLING] == SportWeight(0.5)
    assert plan.inputs.physiology.threshold.bpm == 170


def test_with_no_activity_at_all_the_plan_starts_from_zero() -> None:
    world = PlanWorld(with_runs=False)

    plan = world.built().plan

    assert (plan.inputs.fitness.ctl, plan.inputs.fitness.atl) == (0.0, 0.0)


@pytest.mark.parametrize("with_runs", [False, True])
def test_without_physiological_parameters_nothing_is_attempted_or_saved(with_runs: bool) -> None:
    world = PlanWorld(with_physiology=False, with_runs=with_runs)

    result = world.rebuild(TODAY)

    assert result == PlanNotBuilt(NotBuiltReason.MISSING_PHYSIOLOGY)
    assert world.current() is None
    assert world.spy.replacements == 0


def test_the_last_threshold_pace_and_the_retest_status_come_from_the_tests_history() -> None:
    world = PlanWorld()
    long_ago = TODAY - timedelta(days=100)
    start = datetime(2026, 1, 1, 7, 0, tzinfo=UTC)
    tests = world.profile.threshold_tests
    tests.add(ThresholdTest(fit_identity(101), long_ago - timedelta(days=30), start, 165, 310))
    tests.add(ThresholdTest(fit_identity(102), long_ago, start, 168, 300))
    tests.add(ThresholdTest(fit_identity(103), long_ago + timedelta(days=1), start, 169, None))
    tests.add(ThresholdTest(fit_identity(104), TODAY + timedelta(days=3), start, 170, 250))

    plan = world.built().plan

    assert plan.inputs.threshold_pace_s_per_km == 300  # the latest with a pace, none in the future
    assert isinstance(plan.inputs.retest, Overdue)


# --- Saved only when different --------------------------------------------------------------------
def test_an_identical_plan_is_not_saved_twice() -> None:
    world = PlanWorld()
    first = world.built()

    second = world.built()

    assert (first.saved, second.saved) == (True, False)
    assert world.spy.replacements == 1
    assert second.plan.to_json() == first.plan.to_json()


def test_a_change_of_goals_gives_a_new_plan_saved_in_the_place_of_the_old() -> None:
    world = PlanWorld()
    first = world.built()
    world.add_goal(PlanWorld.race("short", TODAY + timedelta(weeks=6)))

    second = world.built()

    assert second.saved
    assert second.plan.to_json() != first.plan.to_json()
    assert world.stored_plan().to_json() == second.plan.to_json()
    assert world.spy.replacements == 2


def test_an_old_activity_that_arrives_late_changes_the_plan_but_not_the_past() -> None:
    world = PlanWorld()
    first = world.built()
    world.run_on(TODAY - timedelta(days=2), 50)

    second = world.built()

    assert second.saved
    assert second.plan.inputs.fitness.ctl > first.plan.inputs.fitness.ctl
    assert world.past(date(2000, 1, 1), date(2100, 1, 1)) == ()  # same day: nothing became past


# --- The past stays history -----------------------------------------------------------------------
def test_t_piano_04_skipped_sessions_are_kept_as_planned_and_the_ramp_is_not_exceeded() -> None:
    world = PlanWorld()
    first = world.built().plan
    world.today = TODAY + timedelta(days=4)  # four days with nothing done

    second = world.built().plan

    frozen = world.past(TODAY + timedelta(days=1), TODAY + timedelta(days=4))
    assert [canonical_json(f.day) for f in frozen] == plan_days_json(first, 1, 4)
    assert {f.planned_on for f in frozen} == {TODAY}
    assert second.generated_on == TODAY + timedelta(days=4)
    assert second.days[0].day == TODAY + timedelta(days=5)
    assert second.inputs.fitness.ctl < first.inputs.fitness.ctl  # nothing done: the form dropped
    first_week_gain = second.projection[6].ctl - second.inputs.fitness.ctl
    assert first_week_gain <= defaults.MAX_RAMP_PER_WEEK + 1e-9  # the missed load is not made up


def test_the_adherence_compares_what_was_planned_then_with_what_was_done() -> None:
    world = PlanWorld()
    first = world.built().plan
    world.today = TODAY + timedelta(days=4)
    world.run_on(TODAY + timedelta(days=2), 60)  # one of the four days something was done
    world.built()

    rows = world.adherence(TODAY + timedelta(days=1), TODAY + timedelta(days=4))

    assert [r.day for r in rows] == [TODAY + timedelta(days=n) for n in range(1, 5)]
    assert [r.planned_load for r in rows] == [d.load for d in first.days[:4]]
    done = [r.actual_load for r in rows]
    assert done[0] == done[2] == done[3] == 0.0
    assert done[1] > 0.0
    assert any(r.missed_load > 0 for r in rows)


def test_the_adherence_stops_at_today_and_an_inverted_range_is_empty() -> None:
    world = PlanWorld()
    world.built()
    world.today = TODAY + timedelta(days=2)

    rows = world.adherence(TODAY, TODAY + timedelta(days=100))

    assert [r.day for r in rows] == [TODAY + timedelta(days=1), TODAY + timedelta(days=2)]
    assert world.adherence(TODAY + timedelta(days=5), TODAY) == ()


def test_a_second_rebuild_freezes_only_the_days_that_became_past_since() -> None:
    world = PlanWorld()
    first = world.built().plan
    world.today = TODAY + timedelta(days=4)
    second = world.built().plan
    world.today = TODAY + timedelta(days=6)
    world.add_goal(PlanWorld.race("later", RACE_DAY + timedelta(days=40)))

    world.built()

    frozen = world.past(TODAY + timedelta(days=1), TODAY + timedelta(days=6))
    assert [canonical_json(f.day) for f in frozen[:4]] == plan_days_json(first, 1, 4)
    assert [f.planned_on for f in frozen[:4]] == [TODAY] * 4
    assert [canonical_json(f.day) for f in frozen[4:]] == plan_days_json(second, 1, 2)
    assert [f.planned_on for f in frozen[4:]] == [TODAY + timedelta(days=4)] * 2
    assert len(frozen) == 6


def test_the_past_stays_after_the_plan_is_replaced_by_a_refusal() -> None:
    world = PlanWorld()
    first = world.built().plan
    world.remove_goal(PlanWorld.race().goal_id)
    world.today = TODAY + timedelta(days=2)

    result = world.rebuild(world.today)

    assert result == PlanRefused(PlanRefusal(RefusalReason.NO_GOALS), saved=True)
    stored = world.current()
    assert stored == StoredPlan(PlanRefusal(RefusalReason.NO_GOALS), world.today)
    frozen = world.past(TODAY + timedelta(days=1), TODAY + timedelta(days=2))
    assert [canonical_json(f.day) for f in frozen] == plan_days_json(first, 1, 2)
    rows = world.adherence(TODAY + timedelta(days=1), TODAY + timedelta(days=2))
    assert [r.planned_load for r in rows] == [d.load for d in first.days[:2]]


# --- Refusals are states --------------------------------------------------------------------------
def test_a_refusal_is_saved_once_and_then_recognised() -> None:
    world = PlanWorld(with_goal=False)

    first = world.rebuild(TODAY)
    second = world.rebuild(TODAY)
    later = world.rebuild(TODAY + timedelta(days=1))

    refusal = PlanRefusal(RefusalReason.NO_GOALS)
    assert first == PlanRefused(refusal, saved=True)
    assert second == PlanRefused(refusal, saved=False)
    assert later == PlanRefused(refusal, saved=False)  # same reason, nothing to say again
    assert world.current() == StoredPlan(refusal, TODAY)
    assert world.spy.replacements == 1


def test_a_goal_that_is_no_longer_in_the_future_is_a_refusal_naming_it() -> None:
    world = PlanWorld()
    world.built()
    world.today = RACE_DAY

    result = world.rebuild(world.today)

    assert result == PlanRefused(
        PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("marathon",)), saved=True
    )


def test_without_availability_the_plan_is_refused_for_that_reason() -> None:
    world = PlanWorld()
    SetAvailability(world.profile.activities, world.profile.availability)(None)

    result = world.rebuild(TODAY)

    assert result == PlanRefused(PlanRefusal(RefusalReason.NO_AVAILABILITY), saved=True)


def test_an_unreadable_saved_plan_is_replaced_by_the_next_rebuild() -> None:
    world = PlanWorld()

    class Unreadable(SpyPlans):
        def current(self) -> StoredPlan | UnreadablePlan | None:
            return UnreadablePlan()

    world.rebuild = RebuildPlan(
        PlanSources(
            world.profile.activities,
            world.profile.goals,
            world.profile.availability,
            world.profile.session_choices,
            world.profile.physiology,
            world.profile.sport_weights,
            world.profile.threshold_tests,
            Unreadable(world.profile.plans),
            world.profile.injuries,
        ),
        world.overview_now,
    )

    result = world.built()

    assert result.saved
    assert world.stored_plan().to_json() == result.plan.to_json()
    assert CurrentPlan(Unreadable(world.profile.plans))() is None


# --- Injuries and illnesses (T-INF-03) ------------------------------------------------------------
def ramp_window() -> int:
    return injury_defaults.RAMP_AFTER_RETURN_DAYS


def _open_injury(world: PlanWorld, ident: str, zone: BodyZone = BodyZone.SHIN) -> None:
    OpenInjury(world.profile.injuries)(Injury(InjuryId(ident), TODAY, injury_questionnaire(zone)))


def test_an_open_injury_changes_the_plan_and_closing_it_keeps_the_ramp_lower_for_a_while() -> None:
    world = PlanWorld()
    free = world.built().plan
    assert not free.inputs.injuries
    assert dict(free.parameters)["max_ramp_per_week"] == defaults.MAX_RAMP_PER_WEEK

    _open_injury(world, "i1")
    hurt = world.built()
    assert hurt.saved  # the plan changed, so it is saved again
    assert [i.injury_id.value for i in hurt.plan.inputs.injuries] == ["i1"]
    assert (
        dict(hurt.plan.parameters)["max_ramp_per_week"] == defaults.MAX_RAMP_PER_WEEK_AFTER_INJURY
    )
    kinds = {d.session.session_type for d in hurt.plan.days if d.session}
    assert kinds == {SessionType.ALTERNATIVE}  # the shin does not run, the bike and the pool do

    CloseProblem(world.profile.activities, world.profile.injuries)(InjuryId("i1"), TODAY)
    back = world.built().plan
    assert not back.inputs.injuries
    assert back.inputs.after_injury
    assert dict(back.parameters)["max_ramp_per_week"] == defaults.MAX_RAMP_PER_WEEK_AFTER_INJURY

    world.today = TODAY + timedelta(days=ramp_window() + 1)
    later = world.built().plan
    assert not later.inputs.after_injury
    assert dict(later.parameters)["max_ramp_per_week"] == defaults.MAX_RAMP_PER_WEEK


def test_an_illness_stops_the_plan_and_the_end_of_it_brings_the_training_back() -> None:
    world = PlanWorld()
    ReportIllness(world.profile.activities, world.profile.injuries)(
        Illness(InjuryId("flu"), IllnessKind.SYSTEMIC, TODAY)
    )

    stopped = world.built().plan

    assert not any(d.session for d in stopped.days)
    assert any(isinstance(w, PlanHalted) for w in stopped.warnings)
    CloseProblem(world.profile.activities, world.profile.injuries)(
        InjuryId("flu"), TODAY + timedelta(days=4)
    )
    world.today = TODAY + timedelta(days=5)
    assert any(d.session for d in world.built().plan.days)
