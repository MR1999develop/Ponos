# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Ports of the planning feature: where the current plan and the plan of the past days are kept.

Two kinds of data, both derived (the goals and the activities are the source of truth) but kept
because the past cannot be derived again: a plan computed today does not say what was planned
last week.

- The *current plan*: the last result of `build_plan`, a plan or a refusal, with the date it was
  generated on. It is replaced whenever the result changes.
- The *past days*: for every day that has become past, the `PlanDay` the plan of that moment had
  for it (frozen when the first plan that no longer covers the day replaces the one that did). A
  frozen day never changes, so adherence (planned against done) is about what was really planned.

The implementations share the profile's connection (`ActivityIndex.transaction`). Health-related
data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ponos_coach.planning.domain import Plan, PlanDay, PlanRefusal


class PlanStoreError(Exception):
    """Root of the errors of the plan store (unreadable database, failed write)."""


@dataclass(frozen=True, slots=True)
class StoredPlan:
    """The current plan or refusal and the date it was generated on."""

    state: Plan | PlanRefusal
    generated_on: date


@dataclass(frozen=True, slots=True)
class UnreadablePlan:
    """A current plan is saved but cannot be read back (checksum, version or content invalid).

    The plan is derived: the next rebuild replaces it.
    """


@dataclass(frozen=True, slots=True)
class FrozenDay:
    """A past day as it was planned: `planned_on` is when the plan that held it was generated."""

    planned_on: date
    day: PlanDay

    def __post_init__(self) -> None:
        if self.planned_on >= self.day.day:
            raise ValueError("a day is planned before it happens")


class PlanStore(Protocol):
    """The current plan and the frozen past days of the profile."""

    def current(self) -> StoredPlan | UnreadablePlan | None:
        """The current plan or refusal; None if there never was one.

        The saved JSON is input: size, checksum and content are checked, and what fails is
        `UnreadablePlan`, never an exception.

        Raises:
            PlanStoreError: the database cannot be read.
        """
        ...

    def replace_current(self, state: Plan | PlanRefusal, generated_on: date) -> None:
        """Makes this the current plan or refusal.

        Raises:
            ValueError: `state` is a plan generated on another date than `generated_on`.
            PlanStoreError: write failed.
        """
        ...

    def freeze(self, days: Sequence[FrozenDay]) -> None:
        """Keeps these days as they were planned; a day already frozen stays as it is.

        Raises:
            PlanStoreError: write failed.
        """
        ...

    def past_days(self, first: date, last: date) -> Sequence[FrozenDay]:
        """The frozen days from `first` to `last` included, by ascending day.

        A day whose saved text is unreadable (checksum or content) is not here: it is in
        `unreadable_past_days`, so that the history is never short without saying so.

        Raises:
            PlanStoreError: the database cannot be read.
        """
        ...

    def unreadable_past_days(self, first: date, last: date) -> Sequence[date]:
        """The days from `first` to `last` included that are frozen but cannot be read back
        (checksum or content), by ascending day. The frozen history cannot be rebuilt.

        Raises:
            PlanStoreError: the database cannot be read.
        """
        ...
