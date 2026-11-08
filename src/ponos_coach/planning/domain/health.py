# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What an injury or an illness does to the session types the plan may use.

The injury feature says how much running, strength and which pain-free alternatives the path
allows (`PlanRestriction`); this maps that to the plan's session types, among those the user
chose. The plan never invents a type: it only drops what the body should not take.
"""

from __future__ import annotations

from collections.abc import Sequence

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import INTENSE_TYPES, SessionType
from ponos_coach.injury.domain import PlanInjury, PlanRestriction, RunAllowance, restriction_for

# The sessions of the plan's own sport (the others are strength and the alternative session).
_PRIMARY = frozenset(
    {
        SessionType.EASY,
        SessionType.LONG,
        SessionType.MEDIUM,
        SessionType.THRESHOLD,
        SessionType.VO2MAX,
        SessionType.RECOVERY,
    }
)
_RUNNING_SPORTS = (Sport.RUNNING, Sport.TRAIL)
_BY_ALLOWANCE: dict[RunAllowance, frozenset[SessionType]] = {
    RunAllowance.NONE: frozenset(),
    RunAllowance.RECOVERY: frozenset({SessionType.RECOVERY}),
    RunAllowance.EASY: frozenset({SessionType.RECOVERY, SessionType.EASY}),
    RunAllowance.EASY_LONG: frozenset({SessionType.RECOVERY, SessionType.EASY, SessionType.LONG}),
    RunAllowance.FULL: _PRIMARY,
}
# With a restriction the sessions of the sport come before the alternative one: a running step of
# the path must be run, not replaced by a bike ride.
LIMITED_FILLERS = (SessionType.EASY, SessionType.RECOVERY, SessionType.ALTERNATIVE)


def _sport_is_allowed(sport: Sport, restriction: PlanRestriction) -> bool:
    """False if the restriction rules the plan's sport out altogether (running is graded: see
    `_BY_ALLOWANCE`)."""
    if sport in _RUNNING_SPORTS:
        return restriction.running is not RunAllowance.NONE
    if sport is Sport.CYCLING:
        return restriction.cycling
    if sport in (Sport.HIKING, Sport.WALKING):
        return restriction.walking
    return restriction.strength  # strength and "other"


def _primary_types(
    chosen: frozenset[SessionType], sport: Sport, restriction: PlanRestriction
) -> frozenset[SessionType]:
    """The sport's own chosen sessions that the path allows: running by grade, other sports by
    their flag, and no intensity before the last step."""
    primary = chosen & _PRIMARY
    if sport in _RUNNING_SPORTS:
        primary &= _BY_ALLOWANCE[restriction.running]
    elif not _sport_is_allowed(sport, restriction):
        primary = frozenset()
    return primary if restriction.intense else primary - INTENSE_TYPES


def allowed_types(
    chosen: frozenset[SessionType], sport: Sport, restriction: PlanRestriction
) -> frozenset[SessionType]:
    """The chosen types the restriction leaves: none at all when the plan is halted; the sport's
    own sessions as far as the path allows; strength where the zone allows it; the alternative
    session if some pain-free activity is tolerated by every problem."""
    if restriction.halt is not None:
        return frozenset()
    if not restriction.limited_by:
        return chosen
    kept = set(_primary_types(chosen, sport, restriction))
    if SessionType.STRENGTH in chosen and restriction.strength:
        kept.add(SessionType.STRENGTH)
    if SessionType.ALTERNATIVE in chosen and restriction.alternatives:
        kept.add(SessionType.ALTERNATIVE)
    return frozenset(kept)


def restriction_of(injuries: Sequence[PlanInjury]) -> PlanRestriction:
    """The restriction the open injuries and illnesses put on the plan."""
    return restriction_for(injuries)
