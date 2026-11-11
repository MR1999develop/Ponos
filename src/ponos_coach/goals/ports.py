# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the goals feature: the goals, the weekly availability and the chosen session types.

The user's choices (source of truth: the plan is derived from them). The implementations share the
profile's connection (`ActivityIndex.transaction`), so a change that touches several of them is
all or nothing. Health-related data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ponos_coach.goals.domain import (
    FitnessGoal,
    GoalId,
    RaceGoal,
    SessionChoices,
    WeeklyAvailability,
)

type Goal = RaceGoal | FitnessGoal
"""A goal of the calendar: a race, or a CTL to reach on a date."""


class GoalStoreError(Exception):
    """Root of the errors of the goal stores (unreadable data, failed write)."""


class GoalNotFoundError(GoalStoreError):
    """There is no goal with that id; nothing changes."""


class GoalAlreadyExistsError(GoalStoreError):
    """A goal with that id is already saved; nothing changes."""


class RaceDayTakenError(GoalStoreError):
    """Another race is already on that day (the plan cannot have two); nothing changes."""


class GoalStore(Protocol):
    """The goals of the profile, ids chosen by the caller."""

    def add(self, goal: Goal) -> None:
        """Saves a new goal.

        Raises:
            GoalAlreadyExistsError: the id is taken.
            RaceDayTakenError: a race, and another race is on that day.
            GoalStoreError: write failed.
        """
        ...

    def replace(self, goal: Goal) -> None:
        """Replaces the goal with the same id (it may change from race to fitness goal).

        Raises:
            GoalNotFoundError: there is none with that id.
            RaceDayTakenError: a race, and another race is on that day.
            GoalStoreError: write failed.
        """
        ...

    def remove(self, goal_id: GoalId) -> None:
        """Deletes the goal.

        Raises:
            GoalNotFoundError: there is none; nothing changes.
            GoalStoreError: write failed.
        """
        ...

    def get(self, goal_id: GoalId) -> Goal | None:
        """The goal with that id, or None.

        Raises:
            GoalStoreError: unreadable or invalid data.
        """
        ...

    def list_goals(self) -> Sequence[Goal]:
        """All the goals by ascending date, then id.

        Raises:
            GoalStoreError: unreadable or invalid data.
        """
        ...


class AvailabilityStore(Protocol):
    """The weekly availability (one per profile)."""

    def get(self) -> WeeklyAvailability | None:
        """The availability, or None if the user has not set one.

        Raises:
            GoalStoreError: unreadable or invalid data.
        """
        ...

    def set(self, availability: WeeklyAvailability | None) -> None:
        """Saves the availability; None forgets it (back to "not set").

        Raises:
            GoalStoreError: write failed.
        """
        ...


class SessionChoicesStore(Protocol):
    """The session types the user chose for each sport."""

    def get(self) -> SessionChoices:
        """The choices (empty if the user chose nothing).

        Raises:
            GoalStoreError: unreadable or invalid data.
        """
        ...

    def replace(self, choices: SessionChoices) -> None:
        """Replaces all the choices with these, all or nothing.

        Raises:
            GoalStoreError: write failed.
        """
        ...
