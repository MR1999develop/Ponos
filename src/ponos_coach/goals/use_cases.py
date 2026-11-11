# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the goals: add, change, remove, list; the availability and the session types.

Every command returns what it did, and with it what is needed to take it back exactly:

- `AddGoal` gives `GoalAdded`; undo with `RemoveGoal(goal.goal_id)`, redo with `AddGoal(goal)`.
- `ChangeGoal` gives `GoalChanged(before, after)`; undo with `ChangeGoal(before)`, redo with
  `ChangeGoal(after)`.
- `RemoveGoal` gives `GoalRemoved(goal)`; undo with `AddGoal(goal)`.
- `SetAvailability` and `SetSessionChoices` give `*Changed(before, after)`; undo by setting
  `before` again.

Ids are chosen by the caller (the interface makes one per new goal), so a repeated command is
refused instead of duplicated. The plan is not touched here: it follows from the goals when it is
rebuilt (`profiles.RebuildPlan`). Health-related data: never in logs or error messages.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.goals.domain import GoalId, SessionChoices, WeeklyAvailability
from ponos_coach.goals.ports import (
    AvailabilityStore,
    Goal,
    GoalNotFoundError,
    GoalStore,
    SessionChoicesStore,
)


@dataclass(frozen=True, slots=True)
class GoalAdded:
    """A goal was added."""

    goal: Goal


@dataclass(frozen=True, slots=True)
class GoalChanged:
    """A goal was replaced: `before` is how to put it back."""

    before: Goal
    after: Goal


@dataclass(frozen=True, slots=True)
class GoalRemoved:
    """A goal was removed: `goal` is how to put it back."""

    goal: Goal


@dataclass(frozen=True, slots=True)
class AvailabilityChanged:
    """The availability changed; `before` is None if it had not been set."""

    before: WeeklyAvailability | None
    after: WeeklyAvailability | None


@dataclass(frozen=True, slots=True)
class SessionChoicesChanged:
    """The session type choices changed."""

    before: SessionChoices
    after: SessionChoices


class AddGoal:
    """Adds a goal to the calendar."""

    def __init__(self, goals: GoalStore) -> None:
        self._goals = goals

    def __call__(self, goal: Goal) -> GoalAdded:
        """Saves the goal.

        Raises:
            GoalAlreadyExistsError: the id is taken.
            RaceDayTakenError: a race, and another race is on that day.
            GoalStoreError: write failed.
        """
        self._goals.add(goal)
        return GoalAdded(goal)


class ChangeGoal:
    """Replaces a goal (same id) with a new version of it."""

    def __init__(self, activities: ActivityIndex, goals: GoalStore) -> None:
        self._activities = activities
        self._goals = goals

    def __call__(self, goal: Goal) -> GoalChanged:
        """Saves the new version and returns the old one with it.

        Raises:
            GoalNotFoundError: no goal has this id.
            RaceDayTakenError: a race, and another race is on that day; nothing changes.
            GoalStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = self._goals.get(goal.goal_id)
            if before is None:
                raise GoalNotFoundError("no goal with this id")
            self._goals.replace(goal)
        return GoalChanged(before, goal)


class RemoveGoal:
    """Removes a goal from the calendar."""

    def __init__(self, activities: ActivityIndex, goals: GoalStore) -> None:
        self._activities = activities
        self._goals = goals

    def __call__(self, goal_id: GoalId) -> GoalRemoved:
        """Deletes the goal and returns it, to be put back by `AddGoal`.

        Raises:
            GoalNotFoundError: no goal has this id.
            GoalStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            goal = self._goals.get(goal_id)
            if goal is None:
                raise GoalNotFoundError("no goal with this id")
            self._goals.remove(goal_id)
        return GoalRemoved(goal)


class ListGoals:
    """The goals of the profile."""

    def __init__(self, goals: GoalStore) -> None:
        self._goals = goals

    def __call__(self) -> tuple[Goal, ...]:
        """All the goals by ascending date, then id.

        Raises:
            GoalStoreError: unreadable data.
        """
        return tuple(self._goals.list_goals())


class SetAvailability:
    """Sets the weekly availability."""

    def __init__(self, activities: ActivityIndex, store: AvailabilityStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, availability: WeeklyAvailability | None) -> AvailabilityChanged:
        """Saves the availability (None forgets it) and returns the previous one.

        Raises:
            GoalStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = self._store.get()
            self._store.set(availability)
        return AvailabilityChanged(before, availability)


class ReadAvailability:
    """The weekly availability of the profile."""

    def __init__(self, store: AvailabilityStore) -> None:
        self._store = store

    def __call__(self) -> WeeklyAvailability | None:
        """The availability, or None if not set.

        Raises:
            GoalStoreError: unreadable data.
        """
        return self._store.get()


class SetSessionChoices:
    """Sets the session types the plan may use, for each sport."""

    def __init__(self, activities: ActivityIndex, store: SessionChoicesStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, choices: SessionChoices) -> SessionChoicesChanged:
        """Saves the choices and returns the previous ones.

        Raises:
            GoalStoreError: write failed; nothing changes.
        """
        with self._activities.transaction():
            before = self._store.get()
            self._store.replace(choices)
        return SessionChoicesChanged(before, choices)


class ReadSessionChoices:
    """The session types chosen for each sport."""

    def __init__(self, store: SessionChoicesStore) -> None:
        self._store = store

    def __call__(self) -> SessionChoices:
        """The choices (empty if none).

        Raises:
            GoalStoreError: unreadable data.
        """
        return self._store.get()
