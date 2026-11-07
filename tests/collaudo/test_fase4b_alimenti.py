# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-06 from the outside: a hostile food table and the examples of every kind of meal.

The food table is parsed at the boundary: whatever the bytes, the result is a `FoodTable` or a
`FoodTableError`, never another exception, never a repaired value. The examples of a meal are an
illustration of its grams: every one is inside the declared tolerance, made of whole steps of its
food, deterministic, whatever the order of the foods.

Defects found were kept as strict xfail until fixed; none is left.
"""

from __future__ import annotations

import copy
import json
import random
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.nutrition.domain import (
    Food,
    FoodGroup,
    Macros,
    MealItem,
    MealRole,
    MealSlot,
    NutritionGoal,
    Person,
    plan_day,
    suggest_examples,
    tolerance_g,
)
from ponos_coach.nutrition.food_table import (
    FoodTableError,
    parse_food_table,
)
from tests.collaudo.test_fase4b_modello import GOALS, PEOPLE, SCENARIOS, day_of
from tests.food_support import load_food_table

TABLE = load_food_table()
FOOD_KEYS = (
    "id",
    "name",
    "fdc_id",
    "group",
    "roles",
    "kcal_100g",
    "carbs_100g",
    "protein_100g",
    "fat_100g",
    "portion_step_g",
    "max_portion_g",
)
NUMBER_KEYS = ("kcal_100g", "carbs_100g", "protein_100g", "fat_100g")
WHOLE_KEYS = ("fdc_id", "portion_step_g", "max_portion_g")
REPO = Path(__file__).resolve().parents[2]


def shipped() -> dict[str, Any]:
    data = resources.files("ponos_coach.nutrition").joinpath("foods.json").read_bytes()
    return json.loads(data)  # type: ignore[no-any-return]


def with_food(index: int, key: str, value: object) -> bytes:
    document = shipped()
    document["foods"][index][key] = value
    return json.dumps(document).encode("utf-8")


def without_food_key(index: int, key: str) -> bytes:
    document = shipped()
    del document["foods"][index][key]
    return json.dumps(document).encode("utf-8")


# --- A hostile table is refused, never repaired --------------------------------------------


@pytest.mark.parametrize("key", FOOD_KEYS)
def test_t_nut_06_a_food_missing_any_field_is_refused(key: str) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(without_food_key(3, key))


@pytest.mark.parametrize("key", NUMBER_KEYS)
@pytest.mark.parametrize(
    "value",
    [-1, -0.001, -1e300, "12", None, True, [1], {"a": 1}],
    ids=["neg", "tiny-neg", "huge-neg", "text", "null", "bool", "list", "object"],
)
def test_t_nut_06_a_negative_or_ill_typed_value_is_refused(key: str, value: object) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(with_food(3, key, value))


@pytest.mark.parametrize("key", NUMBER_KEYS)
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "1e309"])
def test_t_nut_06_values_that_are_not_finite_numbers_are_refused(key: str, token: str) -> None:
    # json.dumps cannot write them: the text of the file is edited by hand, as an attacker would
    text = json.dumps(shipped())
    index = text.index(f'"{key}"')
    end = text.index(",", index)
    hostile = text[: index + len(key) + 3] + token + text[end:]

    with pytest.raises(FoodTableError):
        parse_food_table(hostile.encode("utf-8"))


@pytest.mark.parametrize("key", WHOLE_KEYS)
@pytest.mark.parametrize("value", [0, -5, 1.5, "5", None, True])
def test_t_nut_06_whole_number_fields_refuse_everything_else(key: str, value: object) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(with_food(3, key, value))


@pytest.mark.parametrize(
    ("step", "maximum"), [(0, 100), (10, 5), (5, 2001), (-5, 100), (10, 0), (3000, 3000)]
)
def test_t_nut_06_inconsistent_portion_limits_are_refused(step: int, maximum: int) -> None:
    document = shipped()
    document["foods"][3]["portion_step_g"] = step
    document["foods"][3]["max_portion_g"] = maximum

    with pytest.raises(FoodTableError):
        parse_food_table(json.dumps(document).encode("utf-8"))


@pytest.mark.parametrize(
    ("carbs", "protein", "fat"),
    [(60, 30, 20), (100.1, 0, 0), (50, 50, 0.01)],
)
def test_t_nut_06_macros_that_weigh_more_than_100_grams_are_refused(
    carbs: float, protein: float, fat: float
) -> None:
    document = shipped()
    food = document["foods"][3]
    food.update(carbs_100g=carbs, protein_100g=protein, fat_100g=fat, kcal_100g=400)

    with pytest.raises(FoodTableError):
        parse_food_table(json.dumps(document).encode("utf-8"))


@pytest.mark.parametrize(
    ("kcal", "accepted"),
    [
        # 20 g carbs + 10 g protein + 5 g fat = 165 kcal; the slack is 15% (24.75) or 15 kcal
        (165, True),
        (189, True),
        (141, True),
        (190, False),
        (140, False),
        (0, False),
        (900, False),
    ],
)
def test_t_nut_06_energy_must_agree_with_the_macros_within_the_atwater_slack(
    kcal: float, accepted: bool
) -> None:
    document = shipped()
    document["foods"][3].update(carbs_100g=20, protein_100g=10, fat_100g=5, kcal_100g=kcal)
    data = json.dumps(document).encode("utf-8")

    if accepted:
        assert len(parse_food_table(data).foods) == len(TABLE.foods)
    else:
        with pytest.raises(FoodTableError):
            parse_food_table(data)


def test_t_nut_06_unknown_groups_roles_and_a_missing_source_are_refused() -> None:
    for key, value in (("group", "dessert"), ("roles", ["brunch"]), ("roles", "main")):
        with pytest.raises(FoodTableError):
            parse_food_table(with_food(0, key, value))
    for field in ("name", "url", "license"):
        document = shipped()
        del document["source"][field]
        with pytest.raises(FoodTableError):
            parse_food_table(json.dumps(document).encode("utf-8"))
    document = shipped()
    del document["source"]
    with pytest.raises(FoodTableError):
        parse_food_table(json.dumps(document).encode("utf-8"))


@pytest.mark.parametrize("value", ["", "2026-13-45", "26-1-1", "2026-02-30", [1], {}])
def test_t_nut_06_a_verification_that_is_not_a_date_is_refused(value: object) -> None:
    document = shipped()
    document["source"]["verified_on"] = value

    with pytest.raises(FoodTableError):
        parse_food_table(json.dumps(document).encode("utf-8"))


def test_t_nut_06_a_food_with_an_empty_name_or_id_is_refused() -> None:
    for key in ("id", "name"):
        with pytest.raises(FoodTableError):
            parse_food_table(with_food(0, key, ""))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x00" * 100,
        b'{"format": 1, "source": {}, "foods": [' + b"1," * 60 + b"1]}",
        b"[" + b"," * 100,
        b'{"format": 1, "format": 2}',
    ],
    ids=["empty", "nuls", "numbers", "broken", "duplicate-key"],
)
def test_t_nut_06_garbage_bytes_only_ever_raise_the_table_error(data: bytes) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(data)


@pytest.mark.parametrize("count", [39, 201, 0])
def test_t_nut_06_the_number_of_foods_is_bounded(count: int) -> None:
    document = shipped()
    base = document["foods"]
    document["foods"] = [
        {**copy.deepcopy(base[i % len(base)]), "id": f"food_{i}"} for i in range(count)
    ]

    with pytest.raises(FoodTableError):
        parse_food_table(json.dumps(document).encode("utf-8"))


def test_t_nut_06_the_lower_and_upper_bounds_of_the_number_of_foods_are_accepted() -> None:
    document = shipped()
    base = document["foods"]
    for count in (40, 200):
        document["foods"] = [
            {**copy.deepcopy(base[i % len(base)]), "id": f"food_{i}"} for i in range(count)
        ]

        assert len(parse_food_table(json.dumps(document).encode("utf-8")).foods) == count


@pytest.mark.parametrize("depth", [100_000])
def test_d_f4b_06_a_deeply_nested_document_is_refused_with_the_table_error(depth: int) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(b"[" * depth + b"]" * depth)


# --- The shipped table: licence and documentation -------------------------------------------


def test_t_nut_06_the_shipped_table_declares_its_source_and_licence() -> None:
    assert TABLE.source.name == "USDA FoodData Central"
    assert TABLE.source.license == "CC0-1.0"
    assert TABLE.source.url.startswith("https://fdc.nal.usda.gov")
    assert 40 <= len(TABLE.foods) <= 200


def test_t_nut_06_every_shipped_food_agrees_with_atwater_and_has_a_real_portion_range() -> None:
    for food in TABLE.foods:
        atwater = 4 * food.carbs_100g + 4 * food.protein_100g + 9 * food.fat_100g
        assert abs(food.kcal_100g - atwater) <= max(15.0, 0.15 * atwater), food.food_id
        assert food.max_portion_g >= 2 * food.portion_step_g, food.food_id
        assert food.name == food.name.strip() and food.name


def test_d_f4b_08_the_food_table_source_and_licence_are_in_the_dependency_register() -> None:
    register = (REPO / "docs" / "dipendenze.md").read_text(encoding="utf-8")

    assert "FoodData Central" in register
    assert "CC0" in register


# --- Examples: every meal, every order ----------------------------------------------------


def check_examples(item: MealItem, foods: tuple[Food, ...]) -> None:
    examples = suggest_examples(item, foods)
    macros = item.macros
    assert len(examples) <= 3
    assert len(set(examples)) == len(examples)
    for example in examples:
        assert abs(example.carbs_g - macros.carbs_g) <= tolerance_g(macros.carbs_g) + 1e-9
        assert abs(example.protein_g - macros.protein_g) <= tolerance_g(macros.protein_g) + 1e-9
        assert abs(example.fat_g - macros.fat_g) <= tolerance_g(macros.fat_g) + 1e-9
        assert min(example.carbs_g, example.protein_g, example.fat_g) >= 0
        for portion in example.portions:
            assert 0 < portion.grams <= portion.food.max_portion_g
            assert portion.grams % portion.food.portion_step_g == 0


@pytest.mark.parametrize("person", PEOPLE, ids=lambda p: f"{p.sex.value}-{p.age_years}")
@pytest.mark.parametrize("goal", GOALS, ids=lambda g: type(g).__name__)
@pytest.mark.parametrize("scenario", list(SCENARIOS))
def test_t_nut_06_the_examples_of_every_meal_of_every_day_respect_the_tolerance(
    person: Person, goal: NutritionGoal, scenario: str
) -> None:
    result = plan_day(day_of(person, goal, SCENARIOS[scenario], 18 * 60.0))

    for item in result.meals:
        check_examples(item, TABLE.foods)


@pytest.mark.parametrize("person", PEOPLE, ids=lambda p: f"{p.sex.value}-{p.age_years}")
def test_t_nut_06_every_meal_of_an_ordinary_rest_day_has_at_least_one_example(
    person: Person,
) -> None:
    for goal in GOALS[:2]:  # maintenance and performance
        for item in plan_day(day_of(person, goal)).meals:
            assert suggest_examples(item, TABLE.foods), (item.slots, item.macros)


def test_t_nut_06_the_examples_do_not_depend_on_the_order_of_the_foods() -> None:
    meals = [
        MealItem((MealSlot.BREAKFAST,), 450, Macros(70, 25, 18)),
        MealItem((MealSlot.LUNCH,), 780, Macros(112, 45, 44)),
        MealItem((MealSlot.DINNER,), 1200, Macros(90, 40, 30)),
        MealItem((MealSlot.POST_TRAINING,), 600, Macros(88, 24, 0)),
        MealItem((MealSlot.DURING_TRAINING,), 360, Macros(120, 0, 0)),
    ]
    for seed in range(5):
        shuffled = list(TABLE.foods)
        random.Random(seed).shuffle(shuffled)
        for meal in meals:
            assert suggest_examples(meal, tuple(shuffled)) == suggest_examples(meal, TABLE.foods)


def test_t_nut_06_a_table_of_one_food_or_of_foods_without_macros_gives_no_example() -> None:
    water = Food("water", "Water", 1, FoodGroup.CARB, frozenset(MealRole), 0, 0, 0, 0, 50, 1000)
    rice = next(f for f in TABLE.foods if f.food_id == "rice_white_cooked")
    meal = MealItem((MealSlot.LUNCH,), 780, Macros(112, 45, 44))

    assert suggest_examples(meal, (water,)) == ()
    assert suggest_examples(meal, (rice,)) == ()  # the protein and the fat cannot be matched


@settings(deadline=None)
@given(
    st.sampled_from(list(MealSlot)),
    st.integers(0, 400),
    st.integers(0, 200),
    st.integers(0, 200),
    st.booleans(),
)
@pytest.mark.collaudo
def test_t_nut_06_any_meal_any_order_stays_inside_the_tolerance(
    slot: MealSlot, carbs: int, protein: int, fat: int, shuffle: bool
) -> None:
    foods = list(TABLE.foods)
    if shuffle:
        random.Random(carbs * 7 + protein).shuffle(foods)
    item = MealItem((slot,), 700, Macros(carbs, protein, fat))

    check_examples(item, tuple(foods))
    assert suggest_examples(item, tuple(foods)) == suggest_examples(item, TABLE.foods)
