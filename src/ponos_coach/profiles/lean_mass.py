# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases of the measured lean mass: set (with an exact undo), remove, list.

The lean mass is an optional dated measurement. When one is valid on a day it replaces the Boer
estimate in the limit on energy availability (see `PlanDayNutrition`). Each change is one
transaction. Health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.nutrition.domain import LeanMass, LeanMassEntry, is_plausible_lean_mass
from ponos_coach.nutrition.ports import LeanMassNotFoundError, LeanMassStore
from ponos_coach.profiles.domain import weight_on
from ponos_coach.profiles.ports import WeightStore


class LeanMassDateInFutureError(ValueError):
    """The lean mass is said to hold from a day that has not come yet."""


class LeanMassNeedsWeightError(ValueError):
    """There is no weight to check the lean mass against: enter the weight first."""


class LeanMassImplausibleError(ValueError):
    """The lean mass does not fit the weight of that day (over 95% or under 40% of it)."""


class LeanMassChangedError(Exception):
    """The entry to undo is not as it was left: it was changed or removed after the change."""


@dataclass(frozen=True, slots=True)
class AppliedLeanMass:
    """What setting the lean mass did: the entry now saved, and the entry of the same day it
    replaced (None if the day had none). It is all `UndoLeanMass` needs to put things back."""

    entry: LeanMassEntry
    replaced: LeanMassEntry | None


def _entry_of(entries: Sequence[LeanMassEntry], day: date) -> LeanMassEntry | None:
    return next((e for e in entries if e.valid_from == day), None)


class SetLeanMass:
    """Sets the measured lean mass from a day (included); the same day replaces the entry.

    `today` (the profile's local date) is injected by the Composition Root.
    """

    def __init__(
        self,
        activities: ActivityIndex,
        weights: WeightStore,
        store: LeanMassStore,
        today: Callable[[], date],
    ) -> None:
        self._activities = activities
        self._weights = weights
        self._store = store
        self._today = today

    def __call__(self, lean_mass: LeanMass, valid_from: date) -> AppliedLeanMass:
        """Saves the measurement and says what it replaced.

        Raises:
            LeanMassDateInFutureError: `valid_from` is after today; nothing was written.
            ValueError: `valid_from` is earlier than 1900-01-01.
            LeanMassNeedsWeightError: no weight is entered.
            LeanMassImplausibleError: the lean mass does not fit the weight of that day.
            NutritionStoreError, ProfileDataError: a store failed; nothing changes.
        """
        if valid_from > self._today():
            raise LeanMassDateInFutureError("the validity day is after today")
        entry = LeanMassEntry(valid_from, lean_mass)
        with self._activities.transaction():
            weight = weight_on(self._weights.list_entries(), valid_from)
            if weight is None:
                raise LeanMassNeedsWeightError("a weight is needed to check the lean mass")
            if not is_plausible_lean_mass(lean_mass, weight.tenths_kg / 10):
                raise LeanMassImplausibleError("the lean mass does not fit the weight")
            replaced = _entry_of(self._store.list_entries(), valid_from)
            self._store.put(entry)
        return AppliedLeanMass(entry, replaced)


class UndoLeanMass:
    """Undoes `SetLeanMass` exactly: removes the entry and puts back the one it replaced."""

    def __init__(self, activities: ActivityIndex, store: LeanMassStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, applied: AppliedLeanMass) -> None:
        """Restores the state before the change.

        Raises:
            LeanMassChangedError: the entry is not as `applied` left it; nothing changes.
            NutritionStoreError: a store failed; nothing changes.
        """
        day = applied.entry.valid_from
        with self._activities.transaction():
            if _entry_of(self._store.list_entries(), day) != applied.entry:
                raise LeanMassChangedError("the lean mass was changed after it was set")
            if applied.replaced is None:
                self._store.remove(day)
            else:
                self._store.put(applied.replaced)


class RemoveLeanMass:
    """Removes the measurement of a day (the days it covered fall back to the one before it, or to
    the Boer estimate if there is none)."""

    def __init__(self, activities: ActivityIndex, store: LeanMassStore) -> None:
        self._activities = activities
        self._store = store

    def __call__(self, valid_from: date) -> LeanMassEntry:
        """Deletes the entry that starts on `valid_from` and returns it (to put it back with
        `SetLeanMass`).

        Raises:
            LeanMassNotFoundError: there is none; nothing changes.
            NutritionStoreError: a store failed; nothing changes.
        """
        with self._activities.transaction():
            found = _entry_of(self._store.list_entries(), valid_from)
            if found is None:
                raise LeanMassNotFoundError("no lean mass starts on that day")
            self._store.remove(valid_from)
        return found


class ListLeanMass:
    """The measured lean mass of the open profile."""

    def __init__(self, store: LeanMassStore) -> None:
        self._store = store

    def __call__(self) -> Sequence[LeanMassEntry]:
        """The entries by ascending date.

        Raises:
            NutritionStoreError: unreadable data.
        """
        return tuple(self._store.list_entries())
