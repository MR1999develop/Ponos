# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Distribution of a day's macros over breakfast, lunch, dinner and the training moments.

The day's totals are split exactly (whole grams): first the intakes around each training session
(pre, during, post), then the remainder over the three regular meals. Regular meals that fall
close to a session are moved after it (or before it when that would be too late), and intakes
closer than 90 minutes become one. The sum of the items is always the day's total.

When the next session is less than 8 hours after the end of one, the "after" is hourly instead of
a single dose: one carbohydrate intake an hour for the first 4 hours (ACSM, ISSN, GJSN), inside
the day's carbohydrate budget, the protein with the first one. Those intakes are never merged.

When the day's carbohydrates do not cover all the intakes, lunch and dinner first keep a minimum
(1 g/kg each) and the rest goes to the intakes in this order: during the session (so that the
hourly guidance stays true), after it, before it. The cut is declared to the caller.
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

    `start_minute` is the local start in minutes after midnight, `duration_min` the moving time
    (it decides which intakes the session needs and how much to eat during it), `net_kcal` the
    energy net of the basal metabolism (already in the day's expenditure) and `load` the weighted
    training load.

    `local_end_minute` is where the session ends on the wall clock, in minutes after the local
    midnight of the start (it can pass 1440): start plus the elapsed time, worked out in UTC and
    then shown in local time, so that a change of daylight saving time does not move the recovery.
    Without it the session ends one moving time after the start.
    """

    start_minute: int
    duration_min: float
    net_kcal: float
    load: float
    local_end_minute: float | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.start_minute <= defaults.LAST_MINUTE_OF_DAY:
            raise ValueError("a session starts within the local day")
        if not (math.isfinite(self.duration_min) and self.duration_min > 0):
            raise ValueError("the duration must be finite and positive")
        if not (math.isfinite(self.net_kcal) and self.net_kcal >= 0):
            raise ValueError("net energy must be finite and not negative")
        if not (math.isfinite(self.load) and self.load >= 0):
            raise ValueError("the load must be finite and not negative")
        end = self.local_end_minute
        if end is not None and not (math.isfinite(end) and end >= self.start_minute):
            raise ValueError("the end must be finite and not before the start")

    @property
    def end_minute(self) -> float:
        """Minutes after midnight at which the session ends (can pass midnight)."""
        if self.local_end_minute is not None:
            return self.local_end_minute
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
    order. `hourly` is only for a during-training item. `refuel_hour` (1 to 4) marks one of the
    hourly intakes after a session that is followed by another within 8 hours: it is the hour after
    the end that the intake covers; only a post-training item has it.
    """

    slots: tuple[MealSlot, ...]
    minute: int
    macros: Macros
    moved: bool = False
    hourly: HourlyIntake | None = None
    refuel_hour: int | None = None

    def __post_init__(self) -> None:
        if not self.slots or len(set(self.slots)) != len(self.slots):
            raise ValueError("an item has at least one slot, each once")
        if self.slots != tuple(sorted(self.slots, key=lambda slot: slot.rank)):
            raise ValueError("the slots are in display order")
        if not 0 <= self.minute <= defaults.LAST_MINUTE_OF_DAY:
            raise ValueError("an item is within the local day")
        if self.hourly is not None and self.slots != (MealSlot.DURING_TRAINING,):
            raise ValueError("only a during-training item has an hourly intake")
        if self.refuel_hour is not None and (
            self.slots != (MealSlot.POST_TRAINING,)
            or not 1 <= self.refuel_hour <= defaults.REFUEL_HOURS
        ):
            raise ValueError("only a post-training item has a refuel hour, from 1 to 4")


@dataclass(frozen=True, slots=True)
class _Moment:
    """A training intake before it is merged with anything."""

    slot: MealSlot
    minute: int
    carbs_g: int
    protein_g: int
    hourly: HourlyIntake | None = None
    duration_min: float = 0.0  # of the session, for a during-training intake
    refuel_hour: int | None = None  # of the hourly refuel after a session, from 1


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
    return _Moment(
        MealSlot.DURING_TRAINING, session.start_minute, carbs, 0, hourly, session.duration_min
    )


def _refuel_hours(minutes_to_next: float) -> int:
    """How many hourly intakes fit before the next session: at most 4, the last at least 30
    minutes before it, and always the first."""
    room = minutes_to_next - defaults.REFUEL_LAST_BEFORE_NEXT_MIN
    fitting = math.floor(room / defaults.REFUEL_STEP_MIN) + 1
    return max(1, min(defaults.REFUEL_HOURS, fitting))


def _post_moments(
    session: TrainingSession, weight_kg: float, minutes_to_next: float | None
) -> list[_Moment]:
    """What to eat after a session: with another session within 8 hours, hourly carbohydrates for
    the first hours (protein with the first); otherwise one dose, with carbohydrates only after a
    long session."""
    protein = round(defaults.POST_PROTEIN_G_PER_KG * weight_kg)
    first = session.end_minute + defaults.POST_LEAD_MIN
    if minutes_to_next is not None and minutes_to_next < defaults.SPEEDY_REFUEL_WITHIN_MIN:
        carbs = round(defaults.REFUEL_CARBS_G_PER_KG_H * weight_kg)
        return [
            _Moment(
                MealSlot.POST_TRAINING,
                _clamp_minute(first + hour * defaults.REFUEL_STEP_MIN),
                carbs,
                protein if hour == 0 else 0,
                refuel_hour=hour + 1,
            )
            for hour in range(_refuel_hours(minutes_to_next))
        ]
    carbs = (
        round(defaults.POST_CARBS_G_PER_KG * weight_kg)
        if session.duration_min >= defaults.LONG_SESSION_MIN
        else 0
    )
    return [_Moment(MealSlot.POST_TRAINING, _clamp_minute(first), carbs, protein)]


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
            m for m in (_pre_moment(session, weight_kg), _during_moment(session)) if m is not None
        ]
        found += _post_moments(session, weight_kg, gap)
    return found


_CARBS_PRIORITY = (MealSlot.DURING_TRAINING, MealSlot.POST_TRAINING, MealSlot.PRE_TRAINING)


def _carbs_within(moments: list[_Moment], budget: int) -> list[int]:
    """Carbohydrates of each moment when only `budget` grams are available: during-training
    intakes first, then post, then pre (earlier sessions first within each kind)."""
    order = sorted(range(len(moments)), key=lambda i: (_CARBS_PRIORITY.index(moments[i].slot), i))
    given = [0] * len(moments)
    left = max(0, budget)
    for i in order:
        given[i] = min(moments[i].carbs_g, left)
        left -= given[i]
    return given


def _hourly_after_cut(moment: _Moment, carbs_g: int) -> HourlyIntake | None:
    """The hourly guidance of a during-training intake, kept in step with the grams it carries
    (None when no carbohydrate is left to give)."""
    hourly = moment.hourly
    if hourly is None or carbs_g == moment.carbs_g:
        return hourly
    per_hour = math.floor(carbs_g * 60.0 / moment.duration_min)
    if per_hour <= 0:
        return None
    return HourlyIntake(per_hour, hourly.fluid_ml, hourly.sodium_mg)


def _fit_moments(
    moments: list[_Moment], day: Macros, reserved_carbs: int
) -> tuple[list[_Moment], bool]:
    """Moments cut so that they fit the day: protein proportionally (rounding down), carbohydrates
    by priority within what is left after `reserved_carbs`, which the main meals keep."""
    protein_sum = sum(m.protein_g for m in moments)
    protein_ratio = min(1.0, day.protein_g / protein_sum) if protein_sum else 1.0
    carbs = _carbs_within(moments, day.carbs_g - reserved_carbs)
    if protein_ratio == 1.0 and carbs == [m.carbs_g for m in moments]:
        return moments, False
    scaled = [
        _Moment(
            m.slot,
            m.minute,
            c,
            math.floor(m.protein_g * protein_ratio),
            _hourly_after_cut(m, c),
            m.duration_min,
            m.refuel_hour,
        )
        for m, c in zip(moments, carbs, strict=True)
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


def _never_merged(item: MealItem) -> bool:
    """During-training items (eaten while training) and the hourly refuel intakes (one an hour by
    design) stay as they are."""
    return item.slots == (MealSlot.DURING_TRAINING,) or item.refuel_hour is not None


def _fuse(items: list[MealItem]) -> list[MealItem]:
    """Merges items closer than 90 minutes (the merged item has the time of the latest one).

    During-training items and hourly refuel intakes are never merged.
    """
    apart = [i for i in items if _never_merged(i)]
    ordered = sorted(
        (i for i in items if not _never_merged(i)),
        key=lambda i: (i.minute, i.slots[0].rank),
    )
    chains: list[list[MealItem]] = []
    for item in ordered:
        if chains and item.minute - chains[-1][-1].minute < defaults.FUSE_GAP_MIN:
            chains[-1].append(item)
        else:
            chains.append([item])
    return [_merged(chain) for chain in chains] + apart


def _lift_main_meals(carbs: list[int], minimum: int) -> list[int]:
    """Carbohydrates of breakfast, lunch and dinner with lunch and dinner at least `minimum`: the
    difference comes from breakfast first, then from the other main meal (never below `minimum`).
    The total does not change."""
    lifted = list(carbs)
    for target in (1, 2):
        need = minimum - lifted[target]
        for donor in (0, 3 - target):
            if need <= 0:
                break
            floor = 0 if donor == 0 else minimum
            taken = min(need, max(0, lifted[donor] - floor))
            lifted[donor] -= taken
            lifted[target] += taken
            need -= taken
    return lifted


def _regular_meals(
    left: Macros,
    meal_times: MealTimes,
    blocked: Sequence[tuple[int, int]],
    main_meal_min_carbs_g: int,
) -> list[MealItem]:
    """Breakfast, lunch and dinner sharing `left`, moved out of the blocked intervals; lunch and
    dinner keep at least `main_meal_min_carbs_g` of carbohydrates."""
    shares = defaults.MEAL_SHARES
    carbs = _lift_main_meals(split_integer(left.carbs_g, shares), main_meal_min_carbs_g)
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

    The result is sorted by time; its macros sum exactly to `day`; no amount is negative; lunch
    and dinner keep at least 1 g/kg of carbohydrates whenever the day has that much; the hourly
    guidance of a during-training item always matches its grams.
    `minutes_to_next_session` is the time from the end of the last session to the first one of
    the following day, if known.
    """
    ordered = sorted(sessions, key=lambda s: s.start_minute)
    main_min = min(
        round(defaults.MAIN_MEAL_MIN_CARBS_G_PER_KG * weight_kg), day.carbs_g // 2
    )  # lunch and dinner each; never more than the day can give
    moments, scaled = _fit_moments(
        _moments(ordered, weight_kg, minutes_to_next_session), day, 2 * main_min
    )
    left = Macros(
        day.carbs_g - sum(m.carbs_g for m in moments),
        day.protein_g - sum(m.protein_g for m in moments),
        day.fat_g,
    )
    items = _regular_meals(left, meal_times, _blocked_intervals(ordered), main_min)
    items += [
        MealItem(
            (m.slot,),
            m.minute,
            Macros(m.carbs_g, m.protein_g),
            hourly=m.hourly,
            refuel_hour=m.refuel_hour,
        )
        for m in moments
        if m.refuel_hour is None or m.carbs_g or m.protein_g  # a cut hourly intake is dropped
    ]
    result = sorted(_fuse(items), key=lambda i: (i.minute, i.slots[0].rank))
    return tuple(result), scaled
