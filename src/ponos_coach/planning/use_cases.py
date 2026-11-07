# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases that read the saved plan: the current one and the days that have become past.

Rebuilding the plan needs the PMC, the goals and the threshold tests, so it lives with the profile
(`profiles.RebuildPlan`). Health-related data: never in logs or error messages.
"""

from __future__ import annotations

from datetime import date

from ponos_coach.planning.ports import FrozenDay, PlanStore, StoredPlan, UnreadablePlan


class CurrentPlan:
    """The plan saved by the last rebuild."""

    def __init__(self, plans: PlanStore) -> None:
        self._plans = plans

    def __call__(self) -> StoredPlan | None:
        """The last plan or refusal with the date it was generated on; None if the plan was
        never built, or if what was saved cannot be read (the next rebuild replaces it).

        Raises:
            PlanStoreError: the database cannot be read.
        """
        stored = self._plans.current()
        return None if stored is None or isinstance(stored, UnreadablePlan) else stored


class PastPlan:
    """What was planned for the days that are now past."""

    def __init__(self, plans: PlanStore) -> None:
        self._plans = plans

    def __call__(self, first: date, last: date) -> tuple[FrozenDay, ...]:
        """The frozen days from `first` to `last` included, by ascending day: each one as the plan
        of the time had it, whatever the plan says now. Days nobody planned are not there.

        Raises:
            PlanStoreError: the database cannot be read.
        """
        return tuple(self._plans.past_days(first, last))
