# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Goals, availability and session types: the commands, their exact undo, and the refusals.

On the in-memory profile; the same stores are tried on SQLite by the contract tests.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
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
from ponos_coach.goals.ports import (
    Goal,
    GoalAlreadyExistsError,
    GoalNotFoundError,
    RaceDayTakenError,
)
from ponos_coach.goals.use_cases import (
    AddGoal,
    ChangeGoal,
    GoalAdded,
    GoalChanged,
    GoalRemoved,
    ListGoals,
    ReadAvailability,
    ReadSessionChoices,
    RemoveGoal,
    SetAvailability,
    SetSessionChoices,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import absolute_path
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.planning.builders import availability

DAY = date(2026, 6, 14)


def a_profile() -> OpenProfile:
    return InMemoryProfileStorage().create(
        absolute_path("p"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "Europe/Rome",
    )


def race(name: str, day: date = DAY, priority: Priority = Priority.A) -> RaceGoal:
    return RaceGoal(GoalId(name), day, priority, Sport.RUNNING, 21_097, None)


class Calendar:
    """The goal use cases wired to one profile."""

    def __init__(self) -> None:
        profile = a_profile()
        self.profile = profile
        self.add = AddGoal(profile.goals)
        self.change = ChangeGoal(profile.activities, profile.goals)
        self.remove = RemoveGoal(profile.activities, profile.goals)
        self.list = ListGoals(profile.goals)


# --- Goals: each command gives back what is needed to take it back -------------------------------
def test_adding_a_goal_lists_it_and_removing_it_takes_it_back_exactly() -> None:
    calendar = Calendar()
    goal = race("r1")

    added = calendar.add(goal)
    removed = calendar.remove(added.goal.goal_id)

    assert added == GoalAdded(goal)
    assert removed == GoalRemoved(goal)
    assert calendar.list() == ()


def test_removing_a_goal_is_undone_by_adding_what_was_returned() -> None:
    calendar = Calendar()
    goal = FitnessGoal(GoalId("f1"), DAY, 61.5)
    calendar.add(goal)

    removed = calendar.remove(goal.goal_id)
    calendar.add(removed.goal)

    assert calendar.list() == (goal,)


def test_changing_a_goal_gives_the_old_version_and_changing_it_back_restores_it() -> None:
    calendar = Calendar()
    before = race("r1")
    after = RaceGoal(GoalId("r1"), DAY + timedelta(days=7), Priority.B, Sport.CYCLING, 90_000, 9000)
    calendar.add(before)

    changed = calendar.change(after)
    assert changed == GoalChanged(before, after)
    assert calendar.list() == (after,)

    calendar.change(changed.before)

    assert calendar.list() == (before,)


def test_changing_an_unknown_goal_is_refused() -> None:
    with pytest.raises(GoalNotFoundError):
        Calendar().change(race("nobody"))


def test_removing_an_unknown_goal_is_refused() -> None:
    with pytest.raises(GoalNotFoundError):
        Calendar().remove(GoalId("nobody"))


def test_the_same_id_cannot_be_added_twice() -> None:
    calendar = Calendar()
    calendar.add(race("r1"))

    with pytest.raises(GoalAlreadyExistsError):
        calendar.add(race("r1", DAY + timedelta(days=1)))

    assert calendar.list() == (race("r1"),)


def test_two_races_on_one_day_are_refused_whether_added_or_moved() -> None:
    calendar = Calendar()
    calendar.add(race("r1"))
    calendar.add(race("r2", DAY + timedelta(days=1), Priority.C))

    with pytest.raises(RaceDayTakenError):
        calendar.add(race("r3"))
    with pytest.raises(RaceDayTakenError):
        calendar.change(race("r2", DAY, Priority.C))

    assert [g.goal_id.value for g in calendar.list()] == ["r1", "r2"]
    assert calendar.list()[1].day == DAY + timedelta(days=1)


def test_a_failed_change_leaves_the_stored_goal_exactly_as_it_was() -> None:
    calendar = Calendar()
    original = race("r2", DAY + timedelta(days=1), Priority.B)
    calendar.add(race("r1"))
    calendar.add(original)

    with pytest.raises(RaceDayTakenError):
        calendar.change(race("r2", DAY, Priority.A))

    assert calendar.profile.goals.get(GoalId("r2")) == original


@st.composite
def _goals(draw: st.DrawFn) -> Goal:
    name = draw(st.sampled_from(["a", "b", "c", "d"]))
    day = DAY + timedelta(days=draw(st.integers(0, 3)))
    if draw(st.booleans()):
        return race(name, day, draw(st.sampled_from(list(Priority))))
    return FitnessGoal(GoalId(name), day, float(draw(st.integers(0, 200))))


@given(st.lists(st.tuples(st.sampled_from(["add", "change", "remove"]), _goals()), max_size=12))
@settings(max_examples=60, deadline=None)
def test_undoing_every_accepted_command_in_reverse_order_restores_the_calendar(
    commands: list[tuple[str, Goal]],
) -> None:
    calendar = Calendar()
    undo: list[tuple[str, Goal | GoalId]] = []
    for kind, goal in commands:
        try:
            if kind == "add":
                undo.append(("remove", calendar.add(goal).goal.goal_id))
            elif kind == "change":
                undo.append(("change", calendar.change(goal).before))
            else:
                undo.append(("add", calendar.remove(goal.goal_id).goal))
        except (GoalAlreadyExistsError, GoalNotFoundError, RaceDayTakenError):
            continue  # a refused command changed nothing and gives nothing to undo

    for kind, value in reversed(undo):
        if kind == "remove":
            assert isinstance(value, GoalId)
            calendar.remove(value)
        elif kind == "change":
            assert not isinstance(value, GoalId)
            calendar.change(value)
        else:
            assert not isinstance(value, GoalId)
            calendar.add(value)

    assert calendar.list() == ()


# --- Availability and session types ---------------------------------------------------------------
def test_availability_set_gives_the_previous_one_and_setting_it_back_restores_it() -> None:
    profile = a_profile()
    set_availability = SetAvailability(profile.activities, profile.availability)
    read = ReadAvailability(profile.availability)
    mine = availability()
    other = WeeklyAvailability((30, 0, 30, 0, 30, 0, 180), 6, 400)

    first = set_availability(mine)
    second = set_availability(other)
    set_availability(second.before)

    assert first.before is None
    assert (second.before, second.after) == (mine, other)
    assert read() == mine
    set_availability(first.before)
    assert read() is None


def test_session_choices_set_gives_the_previous_ones_and_setting_them_back_restores_them() -> None:
    profile = a_profile()
    set_choices = SetSessionChoices(profile.activities, profile.session_choices)
    read = ReadSessionChoices(profile.session_choices)
    easy = SessionChoices.of({Sport.RUNNING: {SessionType.EASY}})
    more = SessionChoices.of({Sport.RUNNING: {SessionType.EASY, SessionType.LONG}})

    first = set_choices(easy)
    second = set_choices(more)

    assert first.before == SessionChoices.of({})
    assert second.before == easy
    assert read() == more
    set_choices(second.before)
    assert read() == easy
