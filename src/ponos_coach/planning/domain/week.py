# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""One week of sessions: which days get which type, and how long each session is."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ponos_coach.goals.domain import SessionType
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import LOADED_PHASES, TAPER_PHASES, Phase
from ponos_coach.planning.domain.sessions import (
    Shape,
    ZoneRates,
    raw_blocks,
    session_load,
    shape_options,
    zone_minutes,
)

_FAR = 7  # distance used when there is no hard day to be near
_FILLERS = (SessionType.EASY, SessionType.ALTERNATIVE, SessionType.RECOVERY)


@dataclass(frozen=True, slots=True)
class DayInfo:
    """A day as the week sees it: phase, minutes available, and whether a race follows."""

    day: date
    phase: Phase
    minutes: int
    race_tomorrow: bool


@dataclass(frozen=True, slots=True)
class WeekSetup:
    """What the week needs to be laid out.

    `week_phase` is the phase of the week's last loaded day (`None` if there is none); `unload`
    marks the fourth week of a mesocycle. `hard_seed` holds the race days and the last hard
    day of the week before: no hard session on them or next to them.
    """

    days: tuple[DayInfo, ...]
    week_phase: Phase | None
    unload: bool
    long_weekday: int
    chosen: frozenset[SessionType]
    hard_seed: frozenset[date]


@dataclass(frozen=True, slots=True)
class Slot:
    """A session to size: a day, a type, the longest it can be, its share of the budget."""

    day: date
    session_type: SessionType
    phase: Phase
    max_minutes: int


@dataclass(frozen=True, slots=True)
class Placed:
    """A sized session on a day, with the phase whose model gives its structure."""

    shape: Shape
    phase: Phase


class Sizer:
    """Sizes sessions to a load budget with the load rates of each session type.

    Keeps the options of each (type, phase, longest) with their loads, as they repeat a lot.
    """

    def __init__(self, rates_of: Mapping[SessionType, ZoneRates]) -> None:
        self._rates_of = rates_of
        self._cache: dict[
            tuple[SessionType, Phase, int], tuple[tuple[Shape, ...], list[float]]
        ] = {}

    def options(
        self, session_type: SessionType, phase: Phase, max_minutes: int
    ) -> tuple[tuple[Shape, ...], list[float]]:
        """The shapes (shortest first) and their loads, which grow with the duration."""
        key = (session_type, phase, max_minutes)
        if key not in self._cache:
            shapes = shape_options(session_type, phase, max_minutes)
            rates = self._rates_of[session_type]
            loads = [session_load(raw_blocks(shape, phase), rates) for shape in shapes]
            self._cache[key] = (shapes, loads)
        return self._cache[key]

    def size(self, slots: Sequence[Slot], budget: float) -> dict[int, Shape]:
        """Sizes the slots (by index) so that their loads add up to at most `budget`.

        Every session gets at least its shortest shape; if the budget cannot pay all of them the
        lowest-priority ones are dropped (latest day first). What is left over is shared by the
        slots in proportion to their share; a slot that cannot use its part (it is at its
        longest) is fixed first and the rest shared again. Loads are rounded down: the budget is
        never exceeded.
        """
        table = {
            i: self.options(s.session_type, s.phase, s.max_minutes) for i, s in enumerate(slots)
        }
        active = self._affordable_set([i for i in table if table[i][0]], slots, table, budget)
        fixed: dict[int, Shape] = {}
        left = budget
        while active:
            spare = left - sum(table[i][1][0] for i in active)
            share = sum(defaults.SESSION_SHARE[slots[i].session_type] for i in active)
            picks = {
                i: bisect_right(
                    table[i][1],
                    table[i][1][0] + spare * defaults.SESSION_SHARE[slots[i].session_type] / share,
                )
                - 1
                for i in active
            }
            capped = [i for i in active if picks[i] == len(table[i][0]) - 1]
            if not capped:
                fixed.update({i: table[i][0][picks[i]] for i in active})
                break
            for i in capped:
                fixed[i] = table[i][0][picks[i]]
                left -= table[i][1][picks[i]]
            active = [i for i in active if i not in capped]
        return fixed

    @staticmethod
    def _affordable_set(
        active: list[int],
        slots: Sequence[Slot],
        table: Mapping[int, tuple[tuple[Shape, ...], list[float]]],
        budget: float,
    ) -> list[int]:
        """The slots whose shortest sessions the budget can pay, dropping the least important."""
        while active and sum(table[i][1][0] for i in active) > budget:
            worst = min(
                active,
                key=lambda i: (
                    defaults.KEEP_PRIORITY[slots[i].session_type],
                    -slots[i].day.toordinal(),
                ),
            )
            active = [i for i in active if i != worst]
        return active


def _distance_to(day: date, hard: set[date]) -> int:
    return min((abs((day - h).days) for h in hard), default=_FAR)


def _place_hard(
    free: list[DayInfo], hard: set[date], taken: dict[date, SessionType], t: SessionType
) -> None:
    """Puts a hard session on the free day farthest from the hard days (not next to one)."""
    options = [d for d in free if d.day not in taken and _distance_to(d.day, hard) >= 2]
    if not options:
        return
    best = max(options, key=lambda d: (_distance_to(d.day, hard), -d.day.toordinal()))
    taken[best.day] = t
    hard.add(best.day)


def _quality_types(setup: WeekSetup, excluded: frozenset[SessionType]) -> list[SessionType]:
    """The intense types wanted this week, within the weekly cap, in scheduling order."""
    if setup.unload or setup.week_phase is None:
        return []
    wanted = [
        t
        for t in defaults.WEEK_QUALITY.get(setup.week_phase, ())
        if t in setup.chosen and t not in excluded
    ]
    cap = (
        defaults.MAX_INTENSE_PER_WEEK_BASE
        if setup.week_phase is Phase.BASE
        else defaults.MAX_INTENSE_PER_WEEK
    )
    return wanted[:cap]


def _filler(chosen: frozenset[SessionType]) -> SessionType | None:
    return next((t for t in _FILLERS if t in chosen), None)


def _place_long(
    free: list[DayInfo], hard: set[date], taken: dict[date, SessionType], weekday: int
) -> None:
    """The long session goes on the long day, if it is free and not next to a hard day."""
    for info in free:
        if info.day.weekday() == weekday and _distance_to(info.day, hard) >= 2:
            taken[info.day] = SessionType.LONG
            hard.add(info.day)


def _place_strength(free: list[DayInfo], hard: set[date], taken: dict[date, SessionType]) -> None:
    """Strength goes on the free day farthest from the hard days."""
    spots = [d for d in free if d.day not in taken]
    if spots:
        best = max(spots, key=lambda d: (_distance_to(d.day, hard), -d.day.toordinal()))
        taken[best.day] = SessionType.STRENGTH


def choose_loaded_slots(setup: WeekSetup, excluded: frozenset[SessionType]) -> list[Slot]:
    """Types for the days with normal load: the long session on the long day, the intense work
    spread apart and never next to a hard day, strength, easy sessions on the remaining days.
    """
    free = [d for d in setup.days if d.phase in LOADED_PHASES and d.minutes > 0]
    hard = set(setup.hard_seed)
    taken: dict[date, SessionType] = {}
    if SessionType.LONG in setup.chosen:
        _place_long(free, hard, taken, setup.long_weekday)
    for session_type in _quality_types(setup, excluded):
        _place_hard(free, hard, taken, session_type)
    if SessionType.STRENGTH in setup.chosen:
        _place_strength(free, hard, taken)
    filler = _filler(setup.chosen)
    slots: list[Slot] = []
    for d in free:
        chosen_type = taken.get(d.day, filler)
        if chosen_type is not None:
            slots.append(Slot(d.day, chosen_type, d.phase, d.minutes))
    return slots


def choose_taper_slots(setup: WeekSetup) -> list[Slot]:
    """One easy session on each available day of an unload before a race; the eve of the race
    is short."""
    filler = _filler(setup.chosen)
    if filler is None:
        return []
    return [
        Slot(
            d.day,
            filler,
            d.phase,
            min(d.minutes, defaults.TAPER_EVE_MAX_MINUTES) if d.race_tomorrow else d.minutes,
        )
        for d in setup.days
        if d.phase in TAPER_PHASES and d.minutes > 0
    ]


def recovery_sessions(setup: WeekSetup) -> dict[date, Placed]:
    """A short recovery session on each recovery day with time for it, if the type is chosen
    (otherwise the day is a rest day)."""
    if SessionType.RECOVERY not in setup.chosen:
        return {}
    step = defaults.ROUND_MINUTES
    out: dict[date, Placed] = {}
    for d in setup.days:
        minutes = min(d.minutes, defaults.RECOVERY_SESSION_MINUTES)
        minutes -= minutes % step
        if d.phase is Phase.RECOVERY and minutes >= defaults.MIN_MINUTES[SessionType.RECOVERY]:
            out[d.day] = Placed(Shape(SessionType.RECOVERY, minutes, 0), d.phase)
    return out


def low_zone_share_ok(placed: Mapping[date, Placed]) -> bool:
    """True if at least 80% of the time of these sessions is in Z1-Z2 (the 80/20 by time)."""
    total = 0
    low = 0
    for item in placed.values():
        zones = zone_minutes(raw_blocks(item.shape, item.phase))
        total += sum(zones)
        low += zones[0] + zones[1]
    return low * 100 >= defaults.LOW_ZONE_SHARE_PERCENT * total


def plan_week(
    setup: WeekSetup, sizer: Sizer, loaded_budget: float, taper_budget: float
) -> dict[date, Placed]:
    """The sessions of a week for the given load budgets.

    In base and maintenance weeks the intense sessions are dropped, last first, until 80% of
    the time is in Z1-Z2.
    """
    excluded: frozenset[SessionType] = frozenset()
    spare = len(defaults.WEEK_QUALITY.get(setup.week_phase or Phase.MAINTENANCE, ())) + 1
    for _ in range(spare):
        slots = choose_loaded_slots(setup, excluded)
        sized = sizer.size(slots, loaded_budget)
        placed = {slots[i].day: Placed(shape, slots[i].phase) for i, shape in sized.items()}
        if setup.week_phase not in (Phase.BASE, Phase.MAINTENANCE) or low_zone_share_ok(placed):
            break
        present = [
            t
            for t in _quality_types(setup, excluded)
            if any(p.shape.session_type is t for p in placed.values())
        ]
        if not present:
            break
        excluded |= {present[-1]}
    taper = choose_taper_slots(setup)
    sized_taper = sizer.size(taper, taper_budget)
    placed.update({taper[i].day: Placed(shape, taper[i].phase) for i, shape in sized_taper.items()})
    placed.update(recovery_sessions(setup))
    return placed
