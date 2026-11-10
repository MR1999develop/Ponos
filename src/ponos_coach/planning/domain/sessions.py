# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Session models: shapes for a type and phase, their zones and their estimated load.

The load uses the load model (TRIMPeq, 100 = one hour at the threshold of the sport) per minute at
the heart rate that stands for each zone (`defaults.ZONE_REPRESENTATIVE_PERCENT` of the threshold).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import ThresholdProtocol, pace_zone_starts_s_per_km
from ponos_coach.goals.domain import SessionType
from ponos_coach.load.domain import (
    Physiology,
    equivalence_factor,
    reference_hour_weight,
    threshold_for_sport,
    trimp_per_minute,
    zone_starts_bpm,
)
from ponos_coach.load.domain.banister import POINTS_PER_THRESHOLD_HOUR
from ponos_coach.planning.domain import defaults
from ponos_coach.planning.domain.phases import Phase
from ponos_coach.planning.domain.plan import Block, BlockRole, PlannedSession

ZoneRates = tuple[float, float, float, float, float]
_ROLES = {
    "warmup": BlockRole.WARMUP,
    "steady": BlockRole.STEADY,
    "cooldown": BlockRole.COOLDOWN,
}


@dataclass(frozen=True, slots=True)
class Shape:
    """A session of a type and duration; `reps` is the number of repetitions of an interval
    session, 0 for a continuous one. `test` makes it the threshold test of that protocol (its
    type is then `THRESHOLD`, its structure fixed: see `threshold_test_shape`)."""

    session_type: SessionType
    minutes: int
    reps: int
    test: ThresholdProtocol | None = None


@dataclass(frozen=True, slots=True)
class RawBlock:
    """A block before the zone ranges are attached."""

    role: BlockRole
    minutes: int
    zone: int
    repeats: int


def _interval_model(session_type: SessionType, phase: Phase) -> defaults.IntervalModel | None:
    """The interval model of a type in a phase (a missing phase uses the build one)."""
    return defaults.INTERVAL_MODELS.get(
        (session_type, phase), defaults.INTERVAL_MODELS.get((session_type, Phase.BUILD))
    )


def shape_options(session_type: SessionType, phase: Phase, max_minutes: int) -> tuple[Shape, ...]:
    """The possible shapes of a type, shortest first, none longer than `max_minutes`.

    Continuous sessions last a multiple of 5 minutes between the type's minimum and maximum;
    interval sessions have 2 to 6 repetitions. Empty if not even the shortest fits.
    """
    model = _interval_model(session_type, phase)
    if model is not None:
        shapes = (
            Shape(session_type, model.total_minutes(r), r)
            for r in range(model.min_reps, model.max_reps + 1)
        )
        return tuple(s for s in shapes if s.minutes <= max_minutes)
    step = defaults.ROUND_MINUTES
    top = min(defaults.MAX_MINUTES[session_type], max_minutes)
    top -= top % step
    return tuple(
        Shape(session_type, minutes, 0)
        for minutes in range(defaults.MIN_MINUTES[session_type], top + 1, step)
    )


def threshold_test_minutes(protocol: ThresholdProtocol) -> int:
    """Duration of the threshold test session: warm-up, the test, cool-down."""
    test = protocol.duration_s // 60
    return defaults.TEST_WARMUP_MINUTES + test + defaults.TEST_COOLDOWN_MINUTES


def threshold_test_shape(protocol: ThresholdProtocol) -> Shape:
    """The threshold test session of a protocol: a hard session of fixed duration."""
    return Shape(SessionType.THRESHOLD, threshold_test_minutes(protocol), 0, protocol)


def _threshold_test_blocks(protocol: ThresholdProtocol) -> tuple[RawBlock, ...]:
    return (
        RawBlock(BlockRole.WARMUP, defaults.TEST_WARMUP_MINUTES, 2, 1),
        RawBlock(BlockRole.WORK, protocol.duration_s // 60, defaults.TEST_ZONE, 1),
        RawBlock(BlockRole.COOLDOWN, defaults.TEST_COOLDOWN_MINUTES, 1, 1),
    )


def raw_blocks(shape: Shape, phase: Phase) -> tuple[RawBlock, ...]:
    """The structure of a shape: warm-up, work, rests, cool-down (see `defaults`); the threshold
    test is a warm-up, one block of the test and a cool-down, whatever the phase."""
    if shape.test is not None:
        return _threshold_test_blocks(shape.test)
    model = _interval_model(shape.session_type, phase)
    if model is None:
        return _continuous_blocks(shape)
    blocks = [RawBlock(BlockRole.WARMUP, model.warmup, 2, 1)]
    blocks.append(RawBlock(BlockRole.WORK, model.work, model.zone, shape.reps))
    if shape.reps > 1:
        blocks.append(RawBlock(BlockRole.REST, model.rest, 1, shape.reps - 1))
    blocks.append(RawBlock(BlockRole.COOLDOWN, model.cooldown, 1, 1))
    return tuple(blocks)


def _continuous_blocks(shape: Shape) -> tuple[RawBlock, ...]:
    parts = defaults.CONTINUOUS_MODELS[shape.session_type]
    blocks: list[RawBlock] = []
    left = shape.minutes
    for index, (role, percent, zone) in enumerate(parts):
        minutes = left if index == len(parts) - 1 else shape.minutes * percent // 100
        blocks.append(RawBlock(_ROLES[role], minutes, zone, 1))
        left -= minutes
    return tuple(blocks)


def zone_minutes(blocks: Sequence[RawBlock | Block]) -> tuple[int, int, int, int, int]:
    """Minutes in each of the five zones."""
    totals = [0, 0, 0, 0, 0]
    for block in blocks:
        totals[block.zone - 1] += block.minutes * block.repeats
    return totals[0], totals[1], totals[2], totals[3], totals[4]


def session_load(blocks: Sequence[RawBlock | Block], rates: ZoneRates) -> float:
    """Estimated load: minutes in each zone times the load per minute of the zone."""
    return math.fsum(m * r for m, r in zip(zone_minutes(blocks), rates, strict=True))


def zone_rates(physiology: Physiology, sport: Sport) -> ZoneRates:
    """Load per minute in each zone for a sport: the TRIMPeq at the zone's representative heart
    rate (the same model as the load of an activity)."""
    limits = physiology.limits
    threshold = threshold_for_sport(physiology, sport)
    k = physiology.sex.banister_k
    factor = equivalence_factor(physiology, sport)
    hour = reference_hour_weight(physiology)

    def rate(percent: int) -> float:
        bpm = min(threshold * percent / 100, float(limits.max_bpm))
        hrr = min(max((bpm - limits.min_bpm) / limits.reserve_bpm, 0.0), 1.0)
        return POINTS_PER_THRESHOLD_HOUR * trimp_per_minute(min(1.0, hrr * factor), k) / hour

    r1, r2, r3, r4, r5 = (rate(p) for p in defaults.ZONE_REPRESENTATIVE_PERCENT)
    return r1, r2, r3, r4, r5


def rates_by_sport(physiology: Physiology, sports: Sequence[Sport]) -> dict[Sport, ZoneRates]:
    """`zone_rates` for each sport."""
    return {s: zone_rates(physiology, s) for s in sports}


@dataclass(frozen=True, slots=True)
class ZoneRanges:
    """Heart-rate and pace range of each zone, whole numbers, for one sport."""

    hr: tuple[tuple[int, int], ...]
    pace: tuple[tuple[int | None, int | None], ...]


def zone_ranges(physiology: Physiology, sport: Sport, pace_s_per_km: int | None) -> ZoneRanges:
    """HR ranges from the zone starts of the sport's threshold; pace ranges from the threshold
    pace for running sports, absent otherwise. A boundary belongs to the higher zone."""
    limits = physiology.limits
    starts = zone_starts_bpm(threshold_for_sport(physiology, sport))
    lows = [limits.min_bpm, *(math.ceil(s) for s in starts)]
    highs = [*(math.ceil(s) - 1 for s in starts), limits.max_bpm]
    hr = tuple((low, max(low, high)) for low, high in zip(lows, highs, strict=True))
    if pace_s_per_km is None or sport not in (Sport.RUNNING, Sport.TRAIL):
        return ZoneRanges(hr, ((None, None),) * 5)
    pace_starts = pace_zone_starts_s_per_km(pace_s_per_km)
    slow = [None, *(math.floor(p) for p in pace_starts)]
    fast = [*(math.floor(p) + 1 for p in pace_starts), None]
    return ZoneRanges(hr, tuple(zip(slow, fast, strict=True)))


@dataclass(frozen=True, slots=True)
class RacePace:
    """The race a session rehearses: its work is in `zone`, at `pace_s_per_km` when known."""

    zone: int
    pace_s_per_km: int | None


_RACE_PACE_ROLES = (BlockRole.WORK, BlockRole.STEADY)


def attach_ranges(
    blocks: Sequence[RawBlock], ranges: ZoneRanges, race_pace: RacePace | None = None
) -> tuple[Block, ...]:
    """Blocks with the HR and pace range of their zone; with `race_pace`, the work blocks in the
    race zone also carry the race pace."""
    out: list[Block] = []
    for raw in blocks:
        low, high = ranges.hr[raw.zone - 1]
        slow, fast = ranges.pace[raw.zone - 1]
        at_race_pace = (
            race_pace is not None and raw.zone == race_pace.zone and raw.role in _RACE_PACE_ROLES
        )
        pace = race_pace.pace_s_per_km if race_pace is not None and at_race_pace else None
        out.append(Block(raw.role, raw.minutes, raw.zone, raw.repeats, low, high, slow, fast, pace))
    return tuple(out)


def make_session(
    shape: Shape,
    phase: Phase,
    sport: Sport,
    rates: ZoneRates,
    ranges: ZoneRanges,
    race_pace: RacePace | None = None,
) -> PlannedSession:
    """The planned session of a shape: structure with zone ranges, and estimated load. With
    `race_pace` it is a race-pace session: its work blocks carry the race pace."""
    raw = raw_blocks(shape, phase)
    return PlannedSession(
        sport,
        shape.session_type,
        shape.minutes,
        attach_ranges(raw, ranges, race_pace),
        session_load(raw, rates),
        race_pace is not None,
        shape.test,
    )


def race_load(expected_time_s: int, rates: ZoneRates) -> float:
    """Estimated load of a race of this duration, from the zone mix of its length."""
    minutes = expected_time_s / 60
    mix = next(shares for limit, shares in defaults.RACE_ZONE_MIX if minutes <= limit)
    return math.fsum(minutes * share * rate for share, rate in zip(mix, rates, strict=True))
