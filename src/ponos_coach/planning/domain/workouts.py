# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Structured workouts for a watch: what a planned session becomes when it is sent to Garmin.

Pure conversion, independent of Garmin's JSON (that lives in the adapter). A session becomes a
flat list of steps: each repetition of an interval session is its own step, work and rest in turn,
so the list says exactly how long the athlete is in each heart-rate range. The heart-rate range of
the zone (whole bpm) is the main target of every step; the pace range is added when the plan has
one (running only), and the work blocks of a race-pace session target the race pace.

The name of every workout the app creates starts with `WORKOUT_PREFIX`, which is how the app tells
its own workouts from the athlete's.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Final

from ponos_coach.activities.domain import Sport
from ponos_coach.planning.domain.plan import Block, BlockRole, PlanDay, PlannedSession

WORKOUT_PREFIX: Final = "Ponos · "  # "Ponos · "
MAX_WORKOUT_NAME_CHARS: Final = 80
MAX_WORKOUT_STEPS: Final = 40
RACE_PACE_TOLERANCE_S_PER_KM: Final = 5  # the pace range around a race pace, each side
_MAX_BPM: Final = 250
_MAX_SECONDS: Final = 86_400


class StepKind(Enum):
    """What a step is for. The value is a stable identifier."""

    WARMUP = "warmup"
    INTERVAL = "interval"
    RECOVERY = "recovery"
    COOLDOWN = "cooldown"


@dataclass(frozen=True, slots=True)
class WorkoutStep:
    """One step: `seconds` long, with a heart-rate range in whole bpm (both ends included) and,
    for a running step with a known pace, the pace range in whole s/km (slowest first)."""

    kind: StepKind
    seconds: int
    hr_low_bpm: int
    hr_high_bpm: int
    pace_slow_s_per_km: int | None = None
    pace_fast_s_per_km: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.seconds <= _MAX_SECONDS:
            raise ValueError("a step lasts from one second to one day")
        if not 0 < self.hr_low_bpm <= self.hr_high_bpm <= _MAX_BPM:
            raise ValueError("the heart-rate range of a step is invalid")
        slow, fast = self.pace_slow_s_per_km, self.pace_fast_s_per_km
        if (slow is None) != (fast is None):
            raise ValueError("a pace range has both ends or none")
        if slow is not None and fast is not None and not 0 < fast <= slow:
            raise ValueError("the pace range of a step is invalid")


@dataclass(frozen=True, slots=True)
class WorkoutSpec:
    """A workout to send: a name that starts with `WORKOUT_PREFIX`, a sport and the steps."""

    name: str
    sport: Sport
    steps: tuple[WorkoutStep, ...]

    def __post_init__(self) -> None:
        if not self.name.startswith(WORKOUT_PREFIX) or len(self.name) > MAX_WORKOUT_NAME_CHARS:
            raise ValueError("the name of an app workout starts with the prefix and is short")
        if not 1 <= len(self.steps) <= MAX_WORKOUT_STEPS:
            raise ValueError("a workout has from 1 to MAX_WORKOUT_STEPS steps")

    @property
    def total_seconds(self) -> int:
        """Total duration of the steps."""
        return sum(step.seconds for step in self.steps)


@dataclass(frozen=True, slots=True)
class ScheduledWorkout:
    """A workout on the calendar of Garmin: the ids Garmin gave it, its name and its day."""

    schedule_id: int
    workout_id: int
    name: str
    day: date

    def __post_init__(self) -> None:
        if self.schedule_id < 1 or self.workout_id < 1:
            raise ValueError("Garmin ids are positive")

    @property
    def is_app_created(self) -> bool:
        """True if the app created it (the name starts with `WORKOUT_PREFIX`)."""
        return self.name.startswith(WORKOUT_PREFIX)


_KIND_OF_ROLE: Final = {
    BlockRole.WARMUP: StepKind.WARMUP,
    BlockRole.WORK: StepKind.INTERVAL,
    BlockRole.STEADY: StepKind.INTERVAL,
    BlockRole.REST: StepKind.RECOVERY,
    BlockRole.COOLDOWN: StepKind.COOLDOWN,
}


def _step_of(block: Block) -> WorkoutStep:
    """One repetition of a block as a step."""
    slow, fast = block.pace_slow_s_per_km, block.pace_fast_s_per_km
    if block.race_pace_s_per_km is not None:
        slow = block.race_pace_s_per_km + RACE_PACE_TOLERANCE_S_PER_KM
        fast = max(1, block.race_pace_s_per_km - RACE_PACE_TOLERANCE_S_PER_KM)
    if slow is None or fast is None:  # a zone open at one end has no pace target
        slow = fast = None
    return WorkoutStep(
        _KIND_OF_ROLE[block.role],
        block.minutes * 60,
        block.hr_low_bpm,
        block.hr_high_bpm,
        slow,
        fast,
    )


def _expand(blocks: tuple[Block, ...]) -> tuple[WorkoutStep, ...]:
    """The steps of the blocks in order. A work block followed by a rest block alternates with it
    (work, rest, work, ..., work); any other block repeats its step `repeats` times."""
    steps: list[WorkoutStep] = []
    index = 0
    while index < len(blocks):
        block = blocks[index]
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if block.role is BlockRole.WORK and following and following.role is BlockRole.REST:
            for repetition in range(block.repeats):
                steps.append(_step_of(block))
                if repetition < following.repeats:
                    steps.append(_step_of(following))
            index += 2
        else:
            steps.extend([_step_of(block)] * block.repeats)
            index += 1
    return tuple(steps)


def workout_from_session(session: PlannedSession) -> WorkoutSpec:
    """The workout of a planned session: its name is the prefix, the type (or "Threshold
    test") and the minutes.

    Total duration and heart-rate ranges are those of the blocks, repetition by repetition.

    Raises:
        ValueError: the session has more steps than a watch takes (`MAX_WORKOUT_STEPS`).
    """
    kind = "threshold test" if session.threshold_test is not None else session.session_type.value
    label = kind.replace("_", " ").capitalize()
    name = f"{WORKOUT_PREFIX}{label} {session.minutes} min"
    return WorkoutSpec(name, session.sport, _expand(session.blocks))


def planned_workouts(days: Iterable[PlanDay]) -> frozenset[tuple[date, str]]:
    """The (day, workout name) of every session the plan has on those days. A session too long
    for a watch is left out: it has no workout."""
    planned: set[tuple[date, str]] = set()
    for item in days:
        if item.session is None:
            continue
        try:
            planned.add((item.day, workout_from_session(item.session).name))
        except ValueError:
            continue
    return frozenset(planned)
