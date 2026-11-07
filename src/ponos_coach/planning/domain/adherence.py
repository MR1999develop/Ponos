# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Planned against done: the comparison that comes before every recalculation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from ponos_coach.planning.domain.plan import Plan


@dataclass(frozen=True, slots=True)
class DayAdherence:
    """The load planned for a day against the load actually done (0 if nothing)."""

    day: date
    planned_load: float
    actual_load: float

    @property
    def missed_load(self) -> float:
        """Load planned and not done (never negative: extra work does not cancel a skipped day)."""
        return max(0.0, self.planned_load - self.actual_load)


def compare_with_actual(
    plan: Plan, actual_daily_load: Mapping[date, float], today: date
) -> tuple[DayAdherence, ...]:
    """Planned against done for each day of the plan up to `today` included.

    The past stays history: a recalculation starts from the PMC of what was really done
    (`build_plan` with the new fitness) and never makes up for `missed_load` beyond the maximum
    ramp. Activities that were not planned count in `actual_load` like the others.
    """
    return tuple(
        DayAdherence(day.day, day.load, actual_daily_load.get(day.day, 0.0))
        for day in plan.days
        if day.day <= today
    )
