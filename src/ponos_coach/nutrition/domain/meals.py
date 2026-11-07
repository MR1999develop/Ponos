# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Distribution of a day's macros over breakfast, lunch, dinner and the training moments.

The day's totals are split exactly (whole grams): first the intakes around each training session
(pre, during, post), then the remainder over the three regular meals. Regular meals that fall
close to a session are moved after it (or before it when that would be too late), and intakes
closer than 90 minutes become one. The sum of the items is always the day's total.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.macros import Macros, split_integer
from ponos_coach.nutrition.domain.settings import MealTimes


class MealSlot(Enum):
    """Which eating occasion an item is; the order of the members is the display order."""

    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    DINNER = "dinner"
    PRE_TRAINING = "pre_training"
    DURING_TRAINING = "during_training"
    POST_TRAINING = "post_training"

    @property
    def rank(self) -> int:
        """Position in the display order."""
        return _SLOT_ORDER.index(self)


_SLOT_ORDER = tuple(MealSlot)


@dataclass(frozen=True, slots=True)
class TrainingSession:
    """A training session as the meal plan needs it.

    `start_minute` is the local start in minutes after midnight, `duration_min` the moving time,
    `net_kcal` the energy net of the basal metabolism (already in the day's expenditure) and
    `load` the weighted training load.
    """

    start_minute: int
    duration_min: float
    net_kcal: float
    load: float

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute <= defaults.LAST_MINUTE_OF_DAY:
            raise ValueError("a session starts within the local day")
        if not (math.isfinite(self.duration_min) and self.duration_min > 0):
            raise ValueError("the duration must be finite and positive")
        if not (math.isfinite(self.net_kcal) and self.net_kcal >= 0):
            raise ValueError("net energy must be finite and not negative")
        if not (math.isfinite(self.load) and self.load >= 0):
            raise ValueError("the load must be finite and not negative")

    @property
    def end_minute(self) -> float:
        """Minutes after midnight at which the session ends (can pass midnight)."""
        return self.start_minute + self.duration_min


@dataclass(frozen=True, slots=True)
class HourlyIntake:
    """Indicative hourly intake during a long session."""

    carbs_g: int
    fluid_ml: int
    sodium_mg: int


@dataclass(frozen=True, slots=True)
class MealItem:
    """One eating occasion of the day: when, what it is, how much.

    A regular meal moved because of training has `moved` set; an item that merges several
    occasions (for example a post-training intake and lunch) lists all their slots in display
    order. `hourly` is only for a during-training item.
    """

    slots: tuple[MealSlot, ...]
    minute: int
    macros: Macros
    moved: bool = False
    hourly: HourlyIntake | None = None

    def __post_init__(self) -> None:
        if not self.slots or len(set(self.slots)) != len(self.slots):
            raise ValueError("an item has at least one slot, each once")
        if self.slots != tuple(sorted(self.slots, key=lambda slot: slot.rank)):
            raise ValueError("the slots are in display order")
        if not 0 <= self.minute <= defaults.LAST_MINUTE_OF_DAY:
            raise ValueError("an item is within the local day")
        if self.hourly is not None and self.slots != (MealSlot.DURING_TRAINING,):
            raise ValueError("only a during-training item has an hourly intake")


@dataclass(frozen=True, slots=True)
class _Moment:
    """A training intake before it is merged with anything."""

    slot: MealSlot
    minute: int
    carbs_g: int
    protein_g: int
    hourly: HourlyIntake | None = None


def _clamp_minute(value: float) -> int:
    return min(max(round(value), 0), defaults.LAST_MINUTE_OF_DAY)


def _pre_moment(session: TrainingSession, weight_kg: float) -> _Moment | None:
    if session.duration_min <= defaults.PRE_MIN_SESSION_MIN:
        return None
    early = session.start_minute - defaults.PRE_LEAD_MIN < defaults.EARLIEST_PRE_MIN
    lead = defaults.PRE_SHORT_LEAD_MIN if early else defaults.PRE_LEAD_MIN
    carbs = round(defaults.PRE_CARBS_G_PER_KG * weight_kg)
    return _Moment(MealSlot.PRE_TRAINING, _clamp_minute(session.start_minute - lead), carbs, 0)


def _during_moment(session: TrainingSession) -> _Moment | None:
    if session.duration_min <= defaults.DURING_MIN_SESSION_MIN:
        return None
    long_effort = session.duration_min > defaults.LONG_EFFORT_MIN
    rate = defaults.DURING_CARBS_G_PER_H_LONG if long_effort else defaults.DURING_CARBS_G_PER_H
    hourly = HourlyIntake(rate, defaults.DURING_FLUID_ML_PER_H, defaults.DURING_SODIUM_MG_PER_H)
    carbs = round(rate * session.duration_min / 60.0)
    return _Moment(MealSlot.DURING_TRAINING, session.start_minute, carbs, 0, hourly)


def _post_moment(
    session: TrainingSession, weight_kg: float, minutes_to_next: float | None
) -> _Moment:
    needs_carbs = session.duration_min >= defaults.LONG_SESSION_MIN or (
        minutes_to_next is not None and minutes_to_next <= defaults.NEXT_SESSION_WITHIN_MIN
    )
    carbs = round(defaults.POST_CARBS_G_PER_KG * weight_kg) if needs_carbs else 0
    protein = round(defaults.POST_PROTEIN_G_PER_KG * weight_kg)
    minute = _clamp_minute(session.end_minute + defaults.POST_LEAD_MIN)
    return _Moment(MealSlot.POST_TRAINING, minute, carbs, protein)


def _moments(
    sessions: Sequence[TrainingSession], weight_kg: float, minutes_to_next: float | None
) -> list[_Moment]:
    """Training intakes of the sessions (sorted by start); short sessions need none."""
    found: list[_Moment] = []
    for i, session in enumerate(sessions):
        if session.duration_min < defaults.MIN_MOMENT_SESSION_MIN:
            continue
        gap = (
            sessions[i + 1].start_minute - session.end_minute
            if i + 1 < len(sessions)
            else minutes_to_next
        )
        found += [
            m
            for m in (
                _pre_moment(session, weight_kg),
                _during_moment(session),
                _post_moment(session, weight_kg, gap),
            )
            if m is not None
        ]
    return found


def _scale_to_fit(moments: list[_Moment], day: Macros) -> tuple[list[_Moment], bool]:
    """Moments cut (rounding down) so that their sum fits the day's carbohydrates and protein."""
    carbs_sum = sum(m.carbs_g for m in moments)
    protein_sum = sum(m.protein_g for m in moments)
    carbs_ratio = min(1.0, day.carbs_g / carbs_sum) if carbs_sum else 1.0
    protein_ratio = min(1.0, day.protein_g / protein_sum) if protein_sum else 1.0
    if carbs_ratio == 1.0 and protein_ratio == 1.0:
        return moments, False
    scaled = [
        _Moment(
            m.slot,
            m.minute,
            math.floor(m.carbs_g * carbs_ratio),
            math.floor(m.protein_g * protein_ratio),
            m.hourly,
        )
        for m in moments
    ]
    return scaled, True


def _blocked_intervals(sessions: Sequence[TrainingSession]) -> list[tuple[int, int]]:
    """Disjoint intervals (open at both ends) in which no regular meal can stay: from one hour
    before a session to one hour after it, overlapping ones merged. Sessions too short to need
    any intake do not block anything."""
    merged: list[tuple[int, int]] = []
    spans = sorted(
        (
            math.floor(s.start_minute - defaults.EATING_BUFFER_MIN),
            math.ceil(s.end_minute + defaults.EATING_BUFFER_MIN),
        )
        for s in sessions
        if s.duration_min >= defaults.MIN_MOMENT_SESSION_MIN
    )
    for low, high in spans:
        if merged and low < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return merged


def _relocated(minute: int, blocked: Sequence[tuple[int, int]]) -> int:
    """Where a regular meal at `minute` goes: unchanged, just after the blocked interval that
    holds it, or just before it if after would be too late (and before is still in the day)."""
    for low, high in blocked:
        if low < minute < high:
            if high <= defaults.LATEST_MEAL_MIN or low < 0:
                return min(high, defaults.LAST_MINUTE_OF_DAY)
            return low
    return minute


def _merged(chain: list[MealItem]) -> MealItem:
    if len(chain) == 1:
        return chain[0]
    slots = tuple(sorted({s for item in chain for s in item.slots}, key=lambda s: s.rank))
    total = Macros()
    for item in chain:
        total = total + item.macros
    return MealItem(slots, chain[-1].minute, total, moved=any(i.moved for i in chain))


def _fuse(items: list[MealItem]) -> list[MealItem]:
    """Merges items closer than 90 minutes (the merged item has the time of the latest one).

    During-training items are never merged: they are eaten while training.
    """
    during = [i for i in items if i.slots == (MealSlot.DURING_TRAINING,)]
    ordered = sorted(
        (i for i in items if i.slots != (MealSlot.DURING_TRAINING,)),
        key=lambda i: (i.minute, i.slots[0].rank),
    )
    chains: list[list[MealItem]] = []
    for item in ordered:
        if chains and item.minute - chains[-1][-1].minute < defaults.FUSE_GAP_MIN:
            chains[-1].append(item)
        else:
            chains.append([item])
    return [_merged(chain) for chain in chains] + during


def _regular_meals(
    left: Macros, meal_times: MealTimes, blocked: Sequence[tuple[int, int]]
) -> list[MealItem]:
    """Breakfast, lunch and dinner sharing `left`, moved out of the blocked intervals."""
    shares = defaults.MEAL_SHARES
    carbs = split_integer(left.carbs_g, shares)
    protein = split_integer(left.protein_g, shares)
    fat = split_integer(left.fat_g, shares)
    slots = (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER)
    meals = []
    for slot, typical, c, p, f in zip(slots, meal_times.minutes, carbs, protein, fat, strict=True):
        minute = _relocated(typical, blocked)
        meals.append(MealItem((slot,), minute, Macros(c, p, f), moved=minute != typical))
    return meals


def schedule_meals(
    day: Macros,
    weight_kg: float,
    meal_times: MealTimes,
    sessions: Sequence[TrainingSession],
    minutes_to_next_session: float | None,
) -> tuple[tuple[MealItem, ...], bool]:
    """Splits the day's macros into items, and says whether training intakes had to be cut.

    The result is sorted by time; its macros sum exactly to `day`; no amount is negative.
    `minutes_to_next_session` is the time from the end of the last session to the first one of
    the following day, if known.
    """
    ordered = sorted(sessions, key=lambda s: s.start_minute)
    moments, scaled = _scale_to_fit(_moments(ordered, weight_kg, minutes_to_next_session), day)
    left = Macros(
        day.carbs_g - sum(m.carbs_g for m in moments),
        day.protein_g - sum(m.protein_g for m in moments),
        day.fat_g,
    )
    items = _regular_meals(left, meal_times, _blocked_intervals(ordered))
    items += [
        MealItem((m.slot,), m.minute, Macros(m.carbs_g, m.protein_g), hourly=m.hourly)
        for m in moments
    ]
    result = sorted(_fuse(items), key=lambda i: (i.minute, i.slots[0].rank))
    return tuple(result), scaled
