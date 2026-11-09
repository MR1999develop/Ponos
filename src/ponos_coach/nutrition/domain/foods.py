# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Common foods and the choice of 2-3 example portions for a meal.

Examples are only an illustration of the grams a meal asks for, never a diet: each example
combines a carbohydrate food, a protein food and optionally a fat food and a vegetable side, with
portions rounded to the food's step, so that each macro of the example is within a declared
tolerance of the meal's (15% of the meal's grams, at least 5 g). The choice is deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.macros import Macros
from ponos_coach.nutrition.domain.meals import MealItem, MealSlot

_PERCENT_MAX = 100.0
_MACRO_SUM_TOLERANCE_G = 0.5  # measured macros of an oil or a sugar may add to a hair over 100 g
_ATWATER_TOLERANCE_FRACTION = 0.15  # a food's kcal must agree with its macros within this...
# ...or this many kcal per 100 g: the energy of the EU labelling counts fibre at 2 kcal/g, which
# is up to about 12 g (24 kcal) per 100 g in the pulses of the table
_ATWATER_TOLERANCE_KCAL = 25.0
_MAX_PORTION_G = 2000


class FoodGroup(Enum):
    """What a food mainly gives; the value is the stable name used in the data file."""

    CARB = "carb"
    PROTEIN = "protein"
    FAT = "fat"
    VEGETABLE = "vegetable"


class MealRole(Enum):
    """The kind of meal a food suits; the value is the stable name used in the data file."""

    BREAKFAST = "breakfast"
    MAIN = "main"  # lunch and dinner
    PRE = "pre"
    DURING = "during"
    POST = "post"


def role_of(slots: Sequence[MealSlot]) -> MealRole:
    """The role an item (possibly merging several slots) is served in: a main meal if it holds
    lunch or dinner, else breakfast, else the training occasion it is."""
    if MealSlot.LUNCH in slots or MealSlot.DINNER in slots:
        return MealRole.MAIN
    if MealSlot.BREAKFAST in slots:
        return MealRole.BREAKFAST
    if MealSlot.DURING_TRAINING in slots:
        return MealRole.DURING
    return MealRole.POST if MealSlot.POST_TRAINING in slots else MealRole.PRE


@dataclass(frozen=True, slots=True)
class Food:
    """A food with its energy and macros per 100 g and where the numbers come from.

    `food_id` is a stable key (also the key of its translated name); `ciqual_code` is the
    ANSES-CIQUAL `alim_code` of the entry the numbers come from. A portion is a multiple of
    `portion_step_g`, at most `max_portion_g`.
    """

    food_id: str
    name: str
    ciqual_code: int
    group: FoodGroup
    roles: frozenset[MealRole]
    kcal_100g: float
    carbs_100g: float
    protein_100g: float
    fat_100g: float
    portion_step_g: int
    max_portion_g: int

    def __post_init__(self) -> None:
        if not self.food_id or not self.name or self.ciqual_code <= 0 or not self.roles:
            raise ValueError("a food has an id, a name, a CIQUAL code and roles")
        values = (self.kcal_100g, self.carbs_100g, self.protein_100g, self.fat_100g)
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError("food values must be finite and not negative")
        if (
            self.carbs_100g + self.protein_100g + self.fat_100g
            > _PERCENT_MAX + _MACRO_SUM_TOLERANCE_G
        ):
            raise ValueError("macros cannot weigh more than the food")
        atwater = 4 * self.carbs_100g + 4 * self.protein_100g + 9 * self.fat_100g
        slack = max(_ATWATER_TOLERANCE_KCAL, _ATWATER_TOLERANCE_FRACTION * atwater)
        if abs(self.kcal_100g - atwater) > slack:
            raise ValueError("food energy does not agree with its macros")
        if not 0 < self.portion_step_g <= self.max_portion_g <= _MAX_PORTION_G:
            raise ValueError("portion limits are inconsistent")


@dataclass(frozen=True, slots=True)
class FoodPortion:
    """A food and a quantity in grams (a multiple of the food's portion step)."""

    food: Food
    grams: int

    def __post_init__(self) -> None:
        if (
            self.grams <= 0
            or self.grams % self.food.portion_step_g
            or (self.grams > self.food.max_portion_g)
        ):
            raise ValueError("the portion is not a valid multiple of the food's step")

    @property
    def carbs_g(self) -> float:
        """Carbohydrate grams of the portion."""
        return self.food.carbs_100g * self.grams / 100.0

    @property
    def protein_g(self) -> float:
        """Protein grams of the portion."""
        return self.food.protein_100g * self.grams / 100.0

    @property
    def fat_g(self) -> float:
        """Fat grams of the portion."""
        return self.food.fat_100g * self.grams / 100.0


@dataclass(frozen=True, slots=True)
class FoodExample:
    """One example of what a meal could be made of, at least one portion."""

    portions: tuple[FoodPortion, ...]

    def __post_init__(self) -> None:
        if not self.portions:
            raise ValueError("an example has at least one portion")

    @property
    def carbs_g(self) -> float:
        """Carbohydrate grams of the whole example."""
        return sum(p.carbs_g for p in self.portions)

    @property
    def protein_g(self) -> float:
        """Protein grams of the whole example."""
        return sum(p.protein_g for p in self.portions)

    @property
    def fat_g(self) -> float:
        """Fat grams of the whole example."""
        return sum(p.fat_g for p in self.portions)


def tolerance_g(wanted_g: int) -> float:
    """How far an example's grams of one macro may be from the meal's: 15%, at least 5 g."""
    return max(float(defaults.FOOD_TOLERANCE_MIN_G), defaults.FOOD_TOLERANCE_FRACTION * wanted_g)


def _round_portion(food: Food, wanted_g: float) -> int:
    """Grams nearest to `wanted_g` that are a multiple of the food's step, at least one step and
    at most the food's maximum."""
    steps = max(1, round(wanted_g / food.portion_step_g))
    return min(
        steps * food.portion_step_g, food.max_portion_g // food.portion_step_g * food.portion_step_g
    )


def _fill(food: Food | None, remaining: float, per_100g: float) -> FoodPortion | None:
    """Portion of `food` that covers `remaining` grams of a macro, if worth a food."""
    if food is None or per_100g <= 0 or remaining < defaults.FOOD_MIN_MACRO_G:
        return None
    return FoodPortion(food, _round_portion(food, remaining / per_100g * 100.0))


def _build(
    meal: Macros,
    carb: Food | None,
    protein: Food | None,
    fat: Food | None,
    side: Food | None,
) -> FoodExample | None:
    """Example made greedily: side, then carbohydrates, protein, fat; None if it does not fit."""
    portions: list[FoodPortion] = []
    if side is not None:
        portions.append(FoodPortion(side, _round_portion(side, defaults.FOOD_VEGETABLE_SIDE_G)))
    wanted = [float(meal.carbs_g), float(meal.protein_g), float(meal.fat_g)]
    picks = ((carb, 0), (protein, 1), (fat, 2))
    for food, macro in picks:
        have = sum((p.carbs_g, p.protein_g, p.fat_g)[macro] for p in portions)
        per_100g = (
            0.0 if food is None else (food.carbs_100g, food.protein_100g, food.fat_100g)[macro]
        )
        portion = _fill(food, wanted[macro] - have, per_100g)
        if portion is not None:
            portions.append(portion)
    if all(p.food.group is FoodGroup.VEGETABLE for p in portions):
        return None  # nothing but the side: the meal asks for no real food
    example = FoodExample(tuple(portions))
    fits = all(
        abs(got - want) <= tolerance_g(wanted_g)
        for got, want, wanted_g in (
            (example.carbs_g, wanted[0], meal.carbs_g),
            (example.protein_g, wanted[1], meal.protein_g),
            (example.fat_g, wanted[2], meal.fat_g),
        )
    )
    return example if fits else None


def _deviation(example: FoodExample, meal: Macros) -> float:
    """How far the example is from the meal, in units of tolerance (lower is better)."""
    return sum(
        abs(got - want) / tolerance_g(want)
        for got, want in (
            (example.carbs_g, meal.carbs_g),
            (example.protein_g, meal.protein_g),
            (example.fat_g, meal.fat_g),
        )
    )


def _pick_diverse(ranked: list[tuple[float, str, FoodExample]]) -> list[FoodExample]:
    """The best examples, preferring ones that do not repeat a main food of the ones kept."""
    chosen: list[FoodExample] = []
    used: set[str] = set()
    for _, _, example in ranked:
        mains = {
            p.food.food_id for p in example.portions if p.food.group is not FoodGroup.VEGETABLE
        }
        if mains.isdisjoint(used) or not chosen:
            chosen.append(example)
            used |= mains
        if len(chosen) == defaults.FOOD_MAX_EXAMPLES:
            return chosen
    for _, _, example in ranked:  # not enough diverse ones: fill with the next best
        if len(chosen) == defaults.FOOD_MAX_EXAMPLES:
            break
        if example not in chosen:
            chosen.append(example)
    return chosen


def suggest_examples(meal: MealItem, foods: Sequence[Food]) -> tuple[FoodExample, ...]:
    """Two or three example portions for an item (fewer if the foods cannot match its grams).

    Every returned example has each macro within `tolerance_g` of the meal's. The result depends
    only on the item and on the foods, in order of `food_id`: deterministic.
    """
    role = role_of(meal.slots)
    pool = sorted((f for f in foods if role in f.roles), key=lambda f: f.food_id)

    def of(group: FoodGroup) -> list[Food | None]:
        found: list[Food | None] = [f for f in pool if f.group is group]
        return found or [None]

    sides: list[Food | None] = of(FoodGroup.VEGETABLE) if role is MealRole.MAIN else [None]
    fats = of(FoodGroup.FAT)
    ranked: list[tuple[float, str, FoodExample]] = []
    for i, (carb, protein) in enumerate(
        (c, p) for c in of(FoodGroup.CARB) for p in of(FoodGroup.PROTEIN)
    ):
        example = _build(meal.macros, carb, protein, fats[i % len(fats)], sides[i % len(sides)])
        if example is not None:
            key = "|".join(p.food.food_id for p in example.portions)
            ranked.append((_deviation(example, meal.macros), key, example))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(_pick_diverse(ranked))
