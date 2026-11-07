# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the nutrition feature: the user's choices, saved in the profile.

The implementations share the profile's connection (`ActivityIndex.transaction`), so a change
that touches several of them is all or nothing. Everything here is health-related data: never
in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from ponos_coach.nutrition.domain import ActivityLevelEntry, GoalPeriod, MealTimes


class NutritionStoreError(Exception):
    """Root of the errors of the nutrition stores (unreadable data, failed write)."""


class GoalPeriodOverlapError(NutritionStoreError):
    """The period shares a day with one that is already saved; nothing changes."""


class GoalPeriodNotFoundError(NutritionStoreError):
    """There is no period starting on that day."""


class ActivityLevelStore(Protocol):
    """Activity level outside sport, with validity date."""

    def put(self, entry: ActivityLevelEntry) -> None:
        """Saves the level valid from `entry.valid_from`; the same date replaces the entry.

        Raises:
            NutritionStoreError: write failed.
        """
        ...

    def list_entries(self) -> Sequence[ActivityLevelEntry]:
        """All the entries by ascending validity date (only one per date).

        Raises:
            NutritionStoreError: unreadable or invalid data.
        """
        ...


class MealTimesStore(Protocol):
    """The typical meal times of the profile (one set)."""

    def get(self) -> MealTimes | None:
        """The saved times, or None if the user never set them (the defaults apply).

        Raises:
            NutritionStoreError: unreadable or invalid data.
        """
        ...

    def set(self, times: MealTimes) -> None:
        """Saves the times, replacing the previous ones.

        Raises:
            NutritionStoreError: write failed.
        """
        ...


class GoalPeriodStore(Protocol):
    """Nutrition goal periods; they never overlap."""

    def list_periods(self) -> Sequence[GoalPeriod]:
        """All the periods by ascending first day.

        Raises:
            NutritionStoreError: unreadable or invalid data.
        """
        ...

    def add(self, period: GoalPeriod) -> None:
        """Saves a period.

        Raises:
            GoalPeriodOverlapError: it shares a day with a saved period; nothing changes.
            NutritionStoreError: write failed.
        """
        ...

    def remove(self, first_day: date) -> None:
        """Deletes the period that starts on `first_day`.

        Raises:
            GoalPeriodNotFoundError: there is none; nothing changes.
            NutritionStoreError: write failed.
        """
        ...
