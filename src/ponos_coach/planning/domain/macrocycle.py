# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The macrocycle: the phase of every day, built backwards from each A race."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import PHASE_RANK, Phase


@dataclass(frozen=True, slots=True)
class Anchor:
    """A goal the macrocycle is built backwards from: an A race or a fitness goal.

    `taper_days` and `peak_days` are 0 for a fitness goal; `recovery_days` is the easy period
    after a race.
    """

    goal_id: str
    day: date
    is_race: bool
    taper_days: int
    peak_days: int
    recovery_days: int
    target_ctl: float


@dataclass(frozen=True, slots=True)
class RampSpan:
    """The days of base and build before a goal, where the CTL climbs towards `target_ctl`.

    Empty (`first_day > last_day`) if there was no room for a block; `last_day` is also the
    day the CTL is checked against the target.
    """

    goal_id: str
    first_day: date
    last_day: date
    target_ctl: float

    @property
    def is_empty(self) -> bool:
        """True if the span has no days."""
        return self.first_day > self.last_day


@dataclass(frozen=True, slots=True)
class CloseRaces:
    """Two consecutive A races without room for a training block between them."""

    first_id: str
    second_id: str
    gap_days: int


@dataclass(frozen=True, slots=True)
class Macrocycle:
    """The phase of each day from `start`, the ramp spans and the A races too close."""

    start: date
    phases: tuple[Phase, ...]
    spans: tuple[RampSpan, ...]
    close_races: tuple[CloseRaces, ...]

    def phase_on(self, day: date) -> Phase:
        """The phase of a day of the plan."""
        return self.phases[(day - self.start).days]


class _Painter:
    """Paints phases on a run of days; a higher-ranked phase is never overwritten."""

    def __init__(self, start: date, end: date) -> None:
        self.start = start
        self.phases = [Phase.MAINTENANCE] * ((end - start).days + 1)

    def paint(self, first: date, last: date, phase: Phase) -> None:
        low = max((first - self.start).days, 0)
        high = min((last - self.start).days, len(self.phases) - 1)
        for index in range(low, high + 1):
            if PHASE_RANK[phase] > PHASE_RANK[self.phases[index]]:
                self.phases[index] = phase


def _paint_block(painter: _Painter, anchor: Anchor, free_first: date) -> RampSpan:
    """Peak, build and base backwards from the end of the free days before the anchor."""
    last = anchor.day - timedelta(days=1) if anchor.is_race else anchor.day
    free_last = last - timedelta(days=anchor.taper_days)
    painter.paint(free_last + timedelta(days=1), last, Phase.TAPER)
    ramp_last = free_last - timedelta(days=anchor.peak_days)
    free = (free_last - free_first).days + 1
    left = free - anchor.peak_days
    empty = RampSpan(anchor.goal_id, ramp_last + timedelta(days=1), ramp_last, anchor.target_ctl)
    if free < defaults.MIN_BLOCK_DAYS or left <= 0:
        return empty
    painter.paint(ramp_last + timedelta(days=1), free_last, Phase.PEAK)
    build = min(defaults.BUILD_MAX_DAYS, left)
    base = min(defaults.BASE_MAX_DAYS, left - build)
    build_first = ramp_last - timedelta(days=build - 1)
    base_first = build_first - timedelta(days=base)
    painter.paint(build_first, ramp_last, Phase.BUILD)
    painter.paint(base_first, build_first - timedelta(days=1), Phase.BASE)
    return RampSpan(anchor.goal_id, base_first, ramp_last, anchor.target_ctl)


def build_macrocycle(
    start: date,
    end: date,
    anchors: Sequence[Anchor],
    b_race_days: Iterable[date],
    race_days: Iterable[date],
    unload_first_days: int,
) -> Macrocycle:
    """Phases for each day from `start` to `end` (inclusive).

    `anchors` are sorted by date. Backwards from each anchor: taper (races), peak (races, 14
    days), build (up to 8 weeks), base (up to 12 weeks); if the free days are fewer than a
    peak's worth they stay maintenance, and so do those before the base. After a race come its
    easy days; before a B race the mini unload; race days last. The first `unload_first_days`
    days are recovery (the adaptation).
    """
    painter = _Painter(start, end)
    spans: list[RampSpan] = []
    close: list[CloseRaces] = []
    previous: Anchor | None = None
    for anchor in anchors:
        lead_in = (
            0 if previous is None else (previous.day - start).days + 1 + previous.recovery_days
        )
        free_first = start + timedelta(days=lead_in)
        span = _paint_block(painter, anchor, free_first)
        spans.append(span)
        if previous is not None and previous.is_race and anchor.is_race and span.is_empty:
            close.append(
                CloseRaces(previous.goal_id, anchor.goal_id, (anchor.day - previous.day).days)
            )
        if anchor.is_race:
            painter.paint(
                anchor.day + timedelta(days=1),
                anchor.day + timedelta(days=anchor.recovery_days),
                Phase.RECOVERY,
            )
        previous = anchor
    for day in b_race_days:
        painter.paint(day - timedelta(days=defaults.MINI_TAPER_DAYS_B), day, Phase.MINI_TAPER)
    painter.paint(start, start + timedelta(days=unload_first_days - 1), Phase.RECOVERY)
    for day in race_days:
        painter.paint(day, day, Phase.RACE)
    return Macrocycle(start, tuple(painter.phases), tuple(spans), tuple(close))
