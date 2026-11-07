# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""One week of sessions: which days get which type, and how long each session is."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ponos_coach.goals.domain import HARD_TYPES, INTENSE_TYPES, SessionType
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import LOADED_PHASES, TAPER_PHASES, Phase, RaceClass
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
_LONG_RACES = (RaceClass.HALF, RaceClass.LONG)


@dataclass(frozen=True, slots=True)
class DayInfo:
    """A day as the week sees it: phase, minutes available, and whether a race follows.

    `race_class` and `days_to_race` describe the next A race after the day (`None` if there is
    none); `taper_fraction` is the share of the reference load that a taper day takes.
    """

    day: date
    phase: Phase
    minutes: int
    race_tomorrow: bool
    taper_fraction: float = 0.0
    race_class: RaceClass | None = None
    days_to_race: int | None = None


@dataclass(frozen=True, slots=True)
class WeekSetup:
    """What the week needs to be laid out.

    `week_phase` is the phase of the week's last loaded day (`None` if there is none); `unload`
    marks the fourth week of a mesocycle. `hard_seed` holds the race days and the last hard
    day of the week before: no hard session on them or next to them. `race_days` are all the
    races of the plan: no intense session in the 72 hours before one. `rotation` (consecutive
    weeks differ by one) makes the intense types alternate.
    """

    days: tuple[DayInfo, ...]
    week_phase: Phase | None
    unload: bool
    long_weekday: int
    chosen: frozenset[SessionType]
    hard_seed: frozenset[date]
    race_days: frozenset[date] = frozenset()
    rotation: int = 0


@dataclass(frozen=True, slots=True)
class Slot:
    """A session to size: a day, a type, the longest it can be, its share of the budget;
    `race_pace` marks work at the pace of the A race."""

    day: date
    session_type: SessionType
    phase: Phase
    max_minutes: int
    race_pace: bool = False


@dataclass(frozen=True, slots=True)
class Placed:
    """A sized session on a day, with the phase whose model gives its structure; `race_pace`
    marks work at the pace of the A race."""

    shape: Shape
    phase: Phase
    race_pace: bool = False


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

    def minimum_load(self, slots: Sequence[Slot]) -> float:
        """The load of the shortest sessions of all the slots (those that have one)."""
        firsts = (self.options(s.session_type, s.phase, s.max_minutes)[1][:1] for s in slots)
        # `sum` of the same floats in the same order as `size`, so that the two agree to the bit
        return sum(load for loads in firsts for load in loads)

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


def _before_race(day: date, race_days: frozenset[date]) -> bool:
    """True if a race falls in the 72 hours after `day` (the next days, up to the limit)."""
    return any(1 <= (r - day).days <= defaults.NO_QUALITY_DAYS_BEFORE_RACE for r in race_days)


def _allowed_for(session_type: SessionType, setup: WeekSetup) -> Callable[[DayInfo], bool]:
    """Where an intense type may go: never in the 72 hours before a race, and no VO2max in the
    peak of a half marathon or a marathon."""

    def allowed(info: DayInfo) -> bool:
        if _before_race(info.day, setup.race_days):
            return False
        long_peak = info.phase is Phase.PEAK and info.race_class in _LONG_RACES
        return not (session_type is SessionType.VO2MAX and long_peak)

    return allowed


def _place_hard(
    free: list[DayInfo],
    hard: set[date],
    taken: dict[date, SessionType],
    session_type: SessionType,
    allowed: Callable[[DayInfo], bool],
) -> None:
    """Puts a hard session on the allowed free day farthest from the hard days (not next to one)."""
    options = [
        d for d in free if d.day not in taken and _distance_to(d.day, hard) >= 2 and allowed(d)
    ]
    if not options:
        return
    best = max(options, key=lambda d: (_distance_to(d.day, hard), -d.day.toordinal()))
    taken[best.day] = session_type
    hard.add(best.day)


def _rotated(types: list[SessionType], turn: int) -> list[SessionType]:
    """The types starting from the `turn`-th, so that they alternate from week to week."""
    if len(types) < 2:
        return types
    first = turn % len(types)
    return types[first:] + types[:first]


def _week_race_class(setup: WeekSetup) -> RaceClass | None:
    loaded = [d for d in setup.days if d.phase in LOADED_PHASES]
    return loaded[-1].race_class if loaded else None


def intense_allowed(sessions: int) -> int:
    """The intense sessions a week of `sessions` sessions may have: a quarter of them, at least
    one from 3 sessions on, none with fewer."""
    if sessions < defaults.MIN_SESSIONS_FOR_INTENSE:
        return 0
    return max(1, sessions * defaults.MAX_INTENSE_SESSION_SHARE_PERCENT // 100)


def _intense_limit(setup: WeekSetup) -> int:
    """The intense sessions the week can take: a share of its possible sessions (a day each, on
    the days with time to train), within the caps."""
    count = sum(1 for d in setup.days if d.minutes > 0 and d.phase in LOADED_PHASES | TAPER_PHASES)
    cap = (
        defaults.MAX_INTENSE_PER_WEEK_BASE
        if setup.week_phase is Phase.BASE
        else defaults.MAX_INTENSE_PER_WEEK
    )
    if count < defaults.FEW_SESSIONS:
        cap = min(cap, defaults.MAX_INTENSE_FEW_SESSIONS)
    return min(cap, intense_allowed(count))


def _quality_types(setup: WeekSetup) -> list[SessionType]:
    """The intense types wanted this week, within the weekly limits, in scheduling order.

    The peak of a half marathon or a marathon has one race-pace type and no VO2max; elsewhere
    the chosen types of the phase alternate week after week.
    """
    phase = setup.week_phase
    if setup.unload or phase is None:
        return []
    race_class = _week_race_class(setup)
    if phase is Phase.PEAK and race_class in _LONG_RACES and race_class is not None:
        wanted = [t for t in defaults.RACE_QUALITY[race_class] if t in setup.chosen][:1]
    else:
        phase_types = [t for t in defaults.WEEK_QUALITY.get(phase, ()) if t in setup.chosen]
        wanted = _rotated(phase_types, setup.rotation)
    return wanted[: _intense_limit(setup)]


def _is_race_pace(session_type: SessionType, info: DayInfo) -> bool:
    """True if an intense session on this day is at the pace of the A race it prepares."""
    return (
        info.phase in (Phase.PEAK, Phase.TAPER)
        and info.race_class is not None
        and session_type is defaults.RACE_PACE_SESSION[info.race_class]
    )


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


def choose_loaded_slots(
    setup: WeekSetup, quality: Sequence[SessionType]
) -> tuple[list[Slot], set[date]]:
    """Types for the days with normal load: the long session on the long day, the `quality`
    sessions spread apart and never next to a hard day, strength, easy sessions on the remaining
    days. Also the hard days of the week (with the seed), for the taper's quality session.
    """
    free = [d for d in setup.days if d.phase in LOADED_PHASES and d.minutes > 0]
    hard = set(setup.hard_seed)
    taken: dict[date, SessionType] = {}
    if SessionType.LONG in setup.chosen:
        _place_long(free, hard, taken, setup.long_weekday)
    for session_type in quality:
        _place_hard(free, hard, taken, session_type, _allowed_for(session_type, setup))
    if SessionType.STRENGTH in setup.chosen:
        _place_strength(free, hard, taken)
    filler = _filler(setup.chosen)
    slots: list[Slot] = []
    for d in free:
        chosen_type = taken.get(d.day, filler)
        if chosen_type is not None:
            race_pace = chosen_type in INTENSE_TYPES and _is_race_pace(chosen_type, d)
            slots.append(Slot(d.day, chosen_type, d.phase, d.minutes, race_pace))
    return slots, hard


def _taper_quality_minutes(info: DayInfo) -> int:
    return min(info.minutes, defaults.TAPER_QUALITY_MAX_MINUTES)


def _taper_quality_type(info: DayInfo, chosen: frozenset[SessionType]) -> SessionType | None:
    """The type of the taper's quality session for the race this day leads to, if chosen."""
    if info.race_class is None:
        return None
    return next((t for t in defaults.RACE_QUALITY[info.race_class] if t in chosen), None)


def _taper_quality_slot(
    setup: WeekSetup, hard: set[date], sizer: Sizer
) -> tuple[date, SessionType] | None:
    """The day and type of the week's shortened quality session in the taper: a taper day more
    than 72 hours before the race, with time for the shortened session, not next to a hard day,
    the farthest from them."""
    options: list[tuple[DayInfo, SessionType]] = []
    for d in setup.days:
        quality = _taper_quality_type(d, setup.chosen)
        if (
            quality is None
            or d.phase is not Phase.TAPER
            or d.days_to_race is None
            or d.days_to_race <= defaults.NO_QUALITY_DAYS_BEFORE_RACE
            or _before_race(d.day, setup.race_days)
            or _distance_to(d.day, hard) < 2
        ):
            continue
        if sizer.options(quality, d.phase, _taper_quality_minutes(d))[0]:
            options.append((d, quality))
    if not options:
        return None
    best = max(options, key=lambda o: (_distance_to(o[0].day, hard), -o[0].day.toordinal()))
    return best[0].day, best[1]


def choose_taper_slots(
    setup: WeekSetup, hard: set[date], sizer: Sizer, *, with_quality: bool
) -> list[Slot]:
    """One easy session on each available day of an unload before a race (so the number of
    sessions is kept); the eve of the race is short. With `with_quality`, one of them is the
    shortened quality session of the week, at race pace for the A race."""
    filler = _filler(setup.chosen)
    if filler is None:
        return []
    quality = _taper_quality_slot(setup, hard, sizer) if with_quality else None
    slots: list[Slot] = []
    for d in setup.days:
        if d.phase not in TAPER_PHASES or d.minutes <= 0:
            continue
        if quality is not None and d.day == quality[0]:
            race_pace = _is_race_pace(quality[1], d)
            slots.append(Slot(d.day, quality[1], d.phase, _taper_quality_minutes(d), race_pace))
            continue
        minutes = min(d.minutes, defaults.TAPER_EVE_MAX_MINUTES) if d.race_tomorrow else d.minutes
        slots.append(Slot(d.day, filler, d.phase, minutes))
    return slots


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


def week_ok(setup: WeekSetup, placed: Mapping[date, Placed]) -> bool:
    """True if the sessions respect the weekly distribution: at most 25% of the sessions
    intense (at least one from 3 sessions, none with fewer; at most 2 with fewer than 6), at most
    3 hard days, and in base and
    maintenance weeks 80% of the time in Z1-Z2."""
    types = [item.shape.session_type for item in placed.values()]
    intense = sum(t in INTENSE_TYPES for t in types)
    if intense > intense_allowed(len(types)):
        return False
    if len(types) < defaults.FEW_SESSIONS and intense > defaults.MAX_INTENSE_FEW_SESSIONS:
        return False
    if sum(t in HARD_TYPES for t in types) > defaults.MAX_HARD_DAYS_PER_WEEK:
        return False
    return setup.week_phase not in (Phase.BASE, Phase.MAINTENANCE) or low_zone_share_ok(placed)


def _placed(slots: Sequence[Slot], sized: Mapping[int, Shape]) -> dict[date, Placed]:
    return {
        slots[i].day: Placed(shape, slots[i].phase, slots[i].race_pace)
        for i, shape in sized.items()
    }


def plan_week(
    setup: WeekSetup,
    sizer: Sizer,
    loaded_budget: float,
    taper_budget: float,
    *,
    keep_frequency: bool = False,
) -> dict[date, Placed]:
    """The sessions of a week for the given load budgets.

    The intense sessions are dropped, the last first (then the taper's quality session), until
    the week respects the distribution (`week_ok`). With `keep_frequency` the taper budget is at
    least what its shortest sessions cost, so that the taper keeps the number of sessions.
    """
    quality = _quality_types(setup)
    keep = len(quality)
    with_taper_quality = True
    while True:
        loaded, hard = choose_loaded_slots(setup, quality[:keep])
        placed = _placed(loaded, sizer.size(loaded, loaded_budget))
        taper = choose_taper_slots(setup, hard, sizer, with_quality=with_taper_quality)
        budget = taper_budget
        if keep_frequency:
            budget = max(budget, sizer.minimum_load(taper))
        placed.update(_placed(taper, sizer.size(taper, budget)))
        placed.update(recovery_sessions(setup))
        if week_ok(setup, placed):
            return placed
        if keep > 0:
            keep -= 1
        elif with_taper_quality and any(s.session_type in INTENSE_TYPES for s in taper):
            with_taper_quality = False
        else:
            return placed
