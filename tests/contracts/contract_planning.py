# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Contracts of the stores of phase 5: goals, availability, session types, current plan, past days.

They are tried through a profile created by the `ProfileStorage` of the `harness` fixture, on the
in-memory fakes and on SQLite alike. Plans are compared by their canonical JSON: SQLite keeps them
rounded to three decimals, the fake keeps the objects.
"""

from __future__ import annotations

from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta

import pytest

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
from ponos_coach.goals.ports import (
    Goal,
    GoalAlreadyExistsError,
    GoalNotFoundError,
    RaceDayTakenError,
)
from ponos_coach.planning.domain import Plan, PlanRefusal, RefusalReason, build_plan
from ponos_coach.planning.domain.serialization import canonical_json
from ponos_coach.planning.ports import FrozenDay, StoredPlan
from tests.contracts.contract_storage import NAME, PROFILE_ID, create
from tests.contracts.harness import StorageHarness
from tests.planning.builders import TODAY, availability, inputs, race

DAY = date(2026, 6, 14)


def a_race(
    name: str = "r1",
    day: date = DAY,
    priority: Priority = Priority.A,
    target: int | None = None,
    sport: Sport = Sport.RUNNING,
) -> RaceGoal:
    return RaceGoal(GoalId(name), day, priority, sport, 10_000, target)


def a_fitness_goal(name: str = "f1", day: date = DAY, ctl: float = 55.5) -> FitnessGoal:
    return FitnessGoal(GoalId(name), day, ctl)


def a_plan() -> Plan:
    """A real plan of the engine for the test athlete, generated on `TODAY`."""
    built = build_plan(inputs([race("a", date(2026, 4, 5))]), TODAY)
    assert isinstance(built, Plan)
    return built


class GoalStoreContract:
    """The goals: ids chosen by the caller, one race a day, form goals free."""

    def test_new_profile_has_no_goals(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.goals.list_goals() == []
            assert profile.goals.get(GoalId("x")) is None

    @pytest.mark.parametrize(
        "goal",
        [
            a_race(),
            a_race(target=3600),
            a_race("r2", priority=Priority.C, sport=Sport.CYCLING),
            a_fitness_goal(),
            a_fitness_goal("f2", ctl=0.0),
            a_fitness_goal("f3", ctl=200.0),
        ],
    )
    def test_a_goal_is_kept_whole(self, harness: StorageHarness, goal: Goal) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(goal)

            assert profile.goals.get(goal.goal_id) == goal
            assert profile.goals.list_goals() == [goal]

    def test_goals_are_listed_by_date_then_id(self, harness: StorageHarness) -> None:
        later = a_race("a", DAY + timedelta(days=5))
        same_day_b = a_fitness_goal("b")
        same_day_a = a_fitness_goal("a2")
        first = a_race("z", DAY - timedelta(days=9), Priority.B)
        with closing(create(harness)) as profile:
            for goal in (later, same_day_b, same_day_a, first):
                profile.goals.add(goal)

            assert profile.goals.list_goals() == [first, same_day_a, same_day_b, later]

    def test_the_same_id_twice_is_refused_and_nothing_changes(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race())

            with pytest.raises(GoalAlreadyExistsError):
                profile.goals.add(a_fitness_goal("r1", DAY + timedelta(days=1)))

            assert profile.goals.list_goals() == [a_race()]

    def test_two_races_on_one_day_are_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race("r1"))

            with pytest.raises(RaceDayTakenError):
                profile.goals.add(a_race("r2", priority=Priority.C))

            assert profile.goals.list_goals() == [a_race("r1")]

    def test_a_form_goal_can_share_the_day_with_a_race_and_with_another_form_goal(
        self, harness: StorageHarness
    ) -> None:
        goals: list[Goal] = [a_race(), a_fitness_goal("f1"), a_fitness_goal("f2", ctl=70.0)]
        with closing(create(harness)) as profile:
            for goal in goals:
                profile.goals.add(goal)

            assert len(profile.goals.list_goals()) == 3

    def test_replace_changes_the_goal_and_may_change_its_kind(
        self, harness: StorageHarness
    ) -> None:
        converted = a_fitness_goal("r1", DAY + timedelta(days=3), 60.0)
        with closing(create(harness)) as profile:
            profile.goals.add(a_race("r1", target=3000))

            profile.goals.replace(converted)

            assert profile.goals.list_goals() == [converted]
            profile.goals.replace(a_race("r1", target=None))
            assert profile.goals.get(GoalId("r1")) == a_race("r1", target=None)

    def test_a_race_can_be_replaced_by_itself_on_its_own_day(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race())

            profile.goals.replace(replace(a_race(), priority=Priority.B))

            assert profile.goals.get(GoalId("r1")) == replace(a_race(), priority=Priority.B)

    def test_replace_unknown_goal_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile, pytest.raises(GoalNotFoundError):
            profile.goals.replace(a_race("nobody"))

    def test_replace_onto_another_races_day_is_refused_and_nothing_changes(
        self, harness: StorageHarness
    ) -> None:
        other_day = a_race("r2", DAY + timedelta(days=1))
        with closing(create(harness)) as profile:
            profile.goals.add(a_race("r1"))
            profile.goals.add(other_day)

            with pytest.raises(RaceDayTakenError):
                profile.goals.replace(a_race("r2", DAY))

            assert profile.goals.get(GoalId("r2")) == other_day

    def test_remove_deletes_and_remove_unknown_is_refused(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race())

            profile.goals.remove(GoalId("r1"))

            assert profile.goals.list_goals() == []
            with pytest.raises(GoalNotFoundError):
                profile.goals.remove(GoalId("r1"))

    def test_a_removed_race_leaves_its_day_free(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race("r1"))
            profile.goals.remove(GoalId("r1"))

            profile.goals.add(a_race("r2"))

            assert profile.goals.list_goals() == [a_race("r2")]

    def test_goals_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        goals: list[Goal] = [a_race(target=3300), a_fitness_goal()]
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")) as profile:
            for goal in goals:
                profile.goals.add(goal)

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.goals.list_goals() == [goals[1], goals[0]]  # same day: by id

    def test_a_transaction_that_fails_leaves_the_goals_as_they_were(
        self, harness: StorageHarness
    ) -> None:
        with closing(create(harness)) as profile:
            profile.goals.add(a_race("r1"))
            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.goals.add(a_race("r2", DAY + timedelta(days=1)))
                profile.goals.remove(GoalId("r1"))
                raise RuntimeError

            assert profile.goals.list_goals() == [a_race("r1")]


class AvailabilityStoreContract:
    """The weekly availability: one per profile, or none."""

    def test_new_profile_has_none(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.availability.get() is None

    def test_it_is_kept_whole_and_replaced(self, harness: StorageHarness) -> None:
        first = availability()
        second = WeeklyAvailability((0, 30, 0, 90, 0, 0, 720), 3, 6000)
        with closing(create(harness)) as profile:
            profile.availability.set(first)
            assert profile.availability.get() == first

            profile.availability.set(second)

            assert profile.availability.get() == second

    def test_setting_none_forgets_it(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.availability.set(availability())

            profile.availability.set(None)

            assert profile.availability.get() is None
            profile.availability.set(None)  # nothing to forget: no error

    def test_it_survives_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")) as profile:
            profile.availability.set(availability())

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.availability.get() == availability()


class SessionChoicesStoreContract:
    """The session types chosen for each sport."""

    def test_new_profile_has_no_choices(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.session_choices.get() == SessionChoices.of({})

    def test_choices_are_kept_whole_and_replaced_all_together(
        self, harness: StorageHarness
    ) -> None:
        first = SessionChoices.of(
            {
                Sport.RUNNING: {SessionType.EASY, SessionType.LONG, SessionType.THRESHOLD},
                Sport.CYCLING: {SessionType.ALTERNATIVE},
            }
        )
        second = SessionChoices.of({Sport.RUNNING: {SessionType.EASY}})
        with closing(create(harness)) as profile:
            profile.session_choices.replace(first)
            assert profile.session_choices.get() == first

            profile.session_choices.replace(second)

            assert profile.session_choices.get() == second

    def test_replacing_with_nothing_clears_them(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            profile.session_choices.replace(SessionChoices.of({Sport.RUNNING: {SessionType.EASY}}))

            profile.session_choices.replace(SessionChoices.of({}))

            assert profile.session_choices.get() == SessionChoices.of({})

    def test_they_survive_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        chosen = SessionChoices.of({Sport.TRAIL: {SessionType.LONG, SessionType.VO2MAX}})
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")) as profile:
            profile.session_choices.replace(chosen)

        with closing(harness.storage.open(folder)) as reopened:
            assert reopened.session_choices.get() == chosen


class PlanStoreContract:
    """The current plan or refusal, and the past days frozen as they were planned."""

    def test_new_profile_has_no_plan_and_no_past(self, harness: StorageHarness) -> None:
        with closing(create(harness)) as profile:
            assert profile.plans.current() is None
            assert profile.plans.past_days(date(2000, 1, 1), date(2100, 1, 1)) == []

    def test_a_plan_is_kept_and_read_back_with_the_same_canonical_text(
        self, harness: StorageHarness
    ) -> None:
        plan = a_plan()
        with closing(create(harness)) as profile:
            profile.plans.replace_current(plan, plan.generated_on)

            stored = profile.plans.current()

        assert isinstance(stored, StoredPlan)
        assert stored.generated_on == plan.generated_on
        assert isinstance(stored.state, Plan)
        assert stored.state.to_json() == plan.to_json()

    @pytest.mark.parametrize(
        "refusal",
        [
            PlanRefusal(RefusalReason.NO_GOALS),
            PlanRefusal(RefusalReason.GOAL_NOT_IN_FUTURE, ("a", "b-2")),
            PlanRefusal(RefusalReason.NO_AVAILABILITY),
        ],
    )
    def test_a_refusal_is_a_state_like_a_plan(
        self, harness: StorageHarness, refusal: PlanRefusal
    ) -> None:
        with closing(create(harness)) as profile:
            profile.plans.replace_current(refusal, TODAY)

            assert profile.plans.current() == StoredPlan(refusal, TODAY)

    def test_a_new_result_replaces_the_old_one(self, harness: StorageHarness) -> None:
        plan = a_plan()
        refusal = PlanRefusal(RefusalReason.NO_GOALS)
        with closing(create(harness)) as profile:
            profile.plans.replace_current(plan, plan.generated_on)
            profile.plans.replace_current(refusal, TODAY + timedelta(days=1))

            assert profile.plans.current() == StoredPlan(refusal, TODAY + timedelta(days=1))

            profile.plans.replace_current(plan, plan.generated_on)
            stored = profile.plans.current()

        assert isinstance(stored, StoredPlan)
        assert isinstance(stored.state, Plan)

    def test_a_plan_generated_on_another_date_is_refused(self, harness: StorageHarness) -> None:
        plan = a_plan()
        with closing(create(harness)) as profile, pytest.raises(ValueError, match="another date"):
            profile.plans.replace_current(plan, plan.generated_on + timedelta(days=1))

    def test_frozen_days_are_listed_by_day_inside_the_range(self, harness: StorageHarness) -> None:
        plan = a_plan()
        frozen = [FrozenDay(plan.generated_on, day) for day in plan.days[:5]]
        with closing(create(harness)) as profile:
            profile.plans.freeze(list(reversed(frozen)))

            everything = profile.plans.past_days(date(2000, 1, 1), date(2100, 1, 1))
            middle = profile.plans.past_days(plan.days[1].day, plan.days[3].day)
            outside = profile.plans.past_days(date(2000, 1, 1), plan.days[0].day - timedelta(1))

        assert [canonical_json(f.day) for f in everything] == [
            canonical_json(f.day) for f in frozen
        ]
        assert [f.planned_on for f in everything] == [plan.generated_on] * 5
        assert [f.day.day for f in middle] == [d.day for d in plan.days[1:4]]
        assert outside == []

    def test_a_frozen_day_never_changes(self, harness: StorageHarness) -> None:
        plan = a_plan()
        original = plan.days[0]
        later_version = replace(original, load=original.load + 10.0)
        with closing(create(harness)) as profile:
            profile.plans.freeze([FrozenDay(plan.generated_on, original)])

            profile.plans.freeze([FrozenDay(plan.generated_on - timedelta(days=1), later_version)])

            [kept] = profile.plans.past_days(original.day, original.day)
        assert kept.planned_on == plan.generated_on
        assert canonical_json(kept.day) == canonical_json(original)

    def test_a_day_cannot_be_planned_on_or_after_itself(self) -> None:
        plan = a_plan()
        day = plan.days[0]

        with pytest.raises(ValueError, match="before it happens"):
            FrozenDay(day.day, day)

    def test_everything_survives_closing_and_reopening(self, harness: StorageHarness) -> None:
        folder = harness.new_folder()
        plan = a_plan()
        with closing(harness.storage.create(folder, PROFILE_ID, NAME, "Europe/Rome")) as profile:
            profile.plans.replace_current(plan, plan.generated_on)
            profile.plans.freeze([FrozenDay(plan.generated_on, plan.days[0])])

        with closing(harness.storage.open(folder)) as reopened:
            stored = reopened.plans.current()
            [kept] = reopened.plans.past_days(plan.days[0].day, plan.days[0].day)

        assert isinstance(stored, StoredPlan)
        assert isinstance(stored.state, Plan)
        assert stored.state.to_json() == plan.to_json()
        assert canonical_json(kept.day) == canonical_json(plan.days[0])

    def test_a_transaction_that_fails_leaves_plan_and_past_as_they_were(
        self, harness: StorageHarness
    ) -> None:
        plan = a_plan()
        with closing(create(harness)) as profile:
            profile.plans.replace_current(plan, plan.generated_on)
            with pytest.raises(RuntimeError), profile.activities.transaction():
                profile.plans.freeze([FrozenDay(plan.generated_on, plan.days[0])])
                profile.plans.replace_current(
                    PlanRefusal(RefusalReason.NO_GOALS), plan.generated_on
                )
                raise RuntimeError

            stored = profile.plans.current()
            past = profile.plans.past_days(date(2000, 1, 1), date(2100, 1, 1))

        assert isinstance(stored, StoredPlan)
        assert isinstance(stored.state, Plan)
        assert past == []
