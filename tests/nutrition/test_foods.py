# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-NUT-06: the food table (source, licence) and example portions within a declared tolerance."""

from __future__ import annotations

import copy
import json
import time
from datetime import date
from importlib import resources
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import (
    ActivityLevel,
    DayInput,
    Food,
    FoodGroup,
    Macros,
    MaintenanceGoal,
    MealItem,
    MealRole,
    MealSlot,
    MealTimes,
    PerformanceGoal,
    Person,
    TrainingSession,
    WeightLossGoal,
    plan_day,
    role_of,
    suggest_examples,
    tolerance_g,
)
from ponos_coach.nutrition.domain.foods import FoodPortion
from ponos_coach.nutrition.food_table import (
    FOOD_TABLE_FORMAT,
    MAX_FOOD_TABLE_BYTES,
    MAX_FOOD_TABLE_DEPTH,
    FoodTable,
    FoodTableError,
    parse_food_table,
)
from tests.food_support import load_food_table

TABLE = load_food_table()
LUNCH = MealItem((MealSlot.LUNCH,), 780, Macros(112, 45, 44))


def raw_document() -> dict[str, Any]:
    """The shipped file as a dictionary, to build hostile variants of it."""
    data = resources.files("ponos_coach.nutrition").joinpath("foods.json").read_bytes()
    return json.loads(data)  # type: ignore[no-any-return]


def encode(document: object) -> bytes:
    return json.dumps(document).encode("utf-8")


# --- The table: source, licence, shape ----------------------------------------------------------


def test_the_shipped_table_has_between_40_and_60_foods_with_a_registered_public_domain_source() -> (
    None
):
    assert 40 <= len(TABLE.foods) <= 60
    assert TABLE.source.name == "USDA FoodData Central"
    assert TABLE.source.license == "CC0-1.0"
    assert TABLE.source.url.startswith("https://fdc.nal.usda.gov")


def test_the_check_against_the_source_is_still_to_do_and_the_file_says_so() -> None:
    assert TABLE.source.verified_on is None


def test_every_food_has_its_food_data_central_id_and_unique_ids() -> None:
    assert all(food.fdc_id > 0 for food in TABLE.foods)
    assert len({food.fdc_id for food in TABLE.foods}) == len(TABLE.foods)
    assert len({food.food_id for food in TABLE.foods}) == len(TABLE.foods)


def test_the_foods_are_sorted_by_id() -> None:
    ids = [food.food_id for food in TABLE.foods]

    assert ids == sorted(ids)


@pytest.mark.parametrize("role", list(MealRole))
def test_every_meal_role_has_foods_to_build_examples_from(role: MealRole) -> None:
    of_role = [f for f in TABLE.foods if role in f.roles]

    assert any(f.group is FoodGroup.CARB for f in of_role)
    if role in (MealRole.MAIN, MealRole.BREAKFAST, MealRole.POST):
        assert any(f.group is FoodGroup.PROTEIN for f in of_role)
    if role is MealRole.MAIN:
        assert any(f.group is FoodGroup.FAT for f in of_role)
        assert any(f.group is FoodGroup.VEGETABLE for f in of_role)


def test_a_sample_of_values_matches_the_published_ones_per_100_g() -> None:
    by_id = {f.food_id: f for f in TABLE.foods}

    assert (by_id["banana"].kcal_100g, by_id["banana"].carbs_100g) == (89.0, 22.8)
    assert by_id["olive_oil"].fat_100g == 100.0
    assert (by_id["chicken_breast_roasted"].protein_100g, by_id["banana"].fdc_id) == (31.0, 173944)


# --- Parsing at the boundary --------------------------------------------------------------------


def test_the_shipped_bytes_parse_to_the_same_table() -> None:
    assert parse_food_table(encode(raw_document())) == TABLE


def test_a_table_over_the_size_cap_is_refused_before_parsing() -> None:
    with pytest.raises(FoodTableError, match="too large"):
        parse_food_table(b" " * (MAX_FOOD_TABLE_BYTES + 1))


@pytest.mark.parametrize("data", [b"", b"not json", b"[]", b'{"format": 1}', b"\xff\xfe"])
def test_a_table_that_is_not_a_valid_document_is_refused(data: bytes) -> None:
    with pytest.raises(FoodTableError):
        parse_food_table(data)


def test_an_unknown_format_version_is_refused() -> None:
    document = raw_document()
    document["format"] = FOOD_TABLE_FORMAT + 1

    with pytest.raises(FoodTableError, match="format"):
        parse_food_table(encode(document))


@pytest.mark.parametrize(
    "change",
    [
        {"fdc_id": 0},  # no identifier
        {"fdc_id": "173944"},  # a text where a number is expected
        {"kcal_100g": -1},
        {"carbs_100g": 60.0, "protein_100g": 60.0},  # more than 100 g of macros per 100 g
        {"kcal_100g": 900},  # energy does not agree with the macros
        {"group": "dessert"},  # unknown group
        {"roles": ["dinner"]},  # unknown role
        {"roles": []},  # no role
        {"portion_step_g": 0},
        {"portion_step_g": 50, "max_portion_g": 10},
        {"name": ""},
    ],
)
def test_a_food_entry_that_is_not_valid_is_refused_never_repaired(
    change: dict[str, object],
) -> None:
    document = raw_document()
    document["foods"][0].update(change)

    with pytest.raises(FoodTableError):
        parse_food_table(encode(document))


def test_repeated_food_ids_are_refused() -> None:
    document = raw_document()
    document["foods"][1]["id"] = document["foods"][0]["id"]

    with pytest.raises(FoodTableError, match="unique"):
        parse_food_table(encode(document))


@pytest.mark.parametrize("count", [0, 39, 201])
def test_too_few_or_too_many_foods_are_refused(count: int) -> None:
    document = raw_document()
    foods = document["foods"]
    document["foods"] = [
        copy.deepcopy(foods[i % len(foods)]) | {"id": f"f{i}"} for i in range(count)
    ]

    with pytest.raises(FoodTableError, match="foods"):
        parse_food_table(encode(document))


def test_a_verification_date_is_read_as_a_date() -> None:
    document = raw_document()
    document["source"]["verified_on"] = "2026-12-01"

    assert parse_food_table(encode(document)).source.verified_on == date(2026, 12, 1)


def test_a_verification_that_is_not_a_date_is_refused() -> None:
    document = raw_document()
    document["source"]["verified_on"] = "tomorrow"

    with pytest.raises(FoodTableError, match="date"):
        parse_food_table(encode(document))


# --- Example portions ----------------------------------------------------------------------------


def test_a_typical_lunch_gets_three_examples_with_every_macro_within_tolerance() -> None:
    examples = suggest_examples(LUNCH, TABLE.foods)

    assert len(examples) == 3
    for example in examples:
        assert abs(example.carbs_g - 112) <= tolerance_g(112)
        assert abs(example.protein_g - 45) <= tolerance_g(45)
        assert abs(example.fat_g - 44) <= tolerance_g(44)


def test_the_tolerance_is_15_percent_of_the_meal_and_at_least_5_grams() -> None:
    assert tolerance_g(0) == 5.0
    assert tolerance_g(20) == 5.0  # 15% would be 3 g
    assert tolerance_g(40) == pytest.approx(6.0)
    assert tolerance_g(200) == pytest.approx(30.0)


def test_portions_are_multiples_of_the_foods_step_and_within_its_maximum() -> None:
    for example in suggest_examples(LUNCH, TABLE.foods):
        for portion in example.portions:
            assert portion.grams % portion.food.portion_step_g == 0
            assert 0 < portion.grams <= portion.food.max_portion_g


def test_the_examples_of_a_main_meal_have_a_vegetable_side_and_distinct_main_foods() -> None:
    examples = suggest_examples(LUNCH, TABLE.foods)

    assert all(
        any(p.food.group is FoodGroup.VEGETABLE for p in example.portions) for example in examples
    )
    mains = [
        {p.food.food_id for p in e.portions if p.food.group is not FoodGroup.VEGETABLE}
        for e in examples
    ]
    assert all(mains[i].isdisjoint(mains[j]) for i in range(3) for j in range(i))


NOT_DURING_A_SESSION = {"sugar_granulated", "bread_white", "bread_whole_wheat", "honey"}


def test_the_examples_during_a_session_come_only_from_foods_marked_for_it() -> None:
    during_foods = {f.food_id for f in TABLE.foods if MealRole.DURING in f.roles}
    assert during_foods
    assert during_foods.isdisjoint(NOT_DURING_A_SESSION)  # never sugar, bread or honey

    for carbs in (30, 60, 100, 180):
        item = MealItem((MealSlot.DURING_TRAINING,), 480, Macros(carbs, 0, 0))
        examples = suggest_examples(item, TABLE.foods)

        assert examples, carbs
        assert all({p.food.food_id for p in e.portions} <= during_foods for e in examples), carbs
        assert all(abs(e.carbs_g - carbs) <= tolerance_g(carbs) for e in examples)
        assert all(abs(e.protein_g) <= tolerance_g(0) for e in examples)


@pytest.mark.parametrize(
    ("slots", "role"),
    [
        ((MealSlot.BREAKFAST,), MealRole.BREAKFAST),
        ((MealSlot.LUNCH,), MealRole.MAIN),
        ((MealSlot.DINNER,), MealRole.MAIN),
        ((MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.POST_TRAINING), MealRole.MAIN),
        ((MealSlot.BREAKFAST, MealSlot.POST_TRAINING), MealRole.BREAKFAST),
        ((MealSlot.PRE_TRAINING,), MealRole.PRE),
        ((MealSlot.DURING_TRAINING,), MealRole.DURING),
        ((MealSlot.POST_TRAINING,), MealRole.POST),
    ],
)
def test_the_role_of_an_item_follows_its_slots(slots: tuple[MealSlot, ...], role: MealRole) -> None:
    assert role_of(slots) is role


def test_an_item_with_nothing_to_eat_gets_no_examples() -> None:
    assert suggest_examples(MealItem((MealSlot.DINNER,), 1200, Macros()), TABLE.foods) == ()


def test_without_foods_there_are_no_examples() -> None:
    assert suggest_examples(LUNCH, ()) == ()


@given(
    st.sampled_from(list(MealSlot)),
    st.integers(0, 250),
    st.integers(0, 120),
    st.integers(0, 120),
)
def test_every_example_ever_returned_respects_the_declared_tolerance(
    slot: MealSlot, carbs: int, protein: int, fat: int
) -> None:
    item = MealItem((slot,), 600, Macros(carbs, protein, fat))

    examples = suggest_examples(item, TABLE.foods)

    assert len(examples) <= 3
    for example in examples:
        assert abs(example.carbs_g - carbs) <= tolerance_g(carbs)
        assert abs(example.protein_g - protein) <= tolerance_g(protein)
        assert abs(example.fat_g - fat) <= tolerance_g(fat)
        for portion in example.portions:
            assert portion.grams % portion.food.portion_step_g == 0


@given(st.integers(0, 250), st.integers(0, 120), st.integers(0, 120))
def test_the_choice_of_examples_is_deterministic(carbs: int, protein: int, fat: int) -> None:
    item = MealItem((MealSlot.DINNER,), 1200, Macros(carbs, protein, fat))

    assert suggest_examples(item, TABLE.foods) == suggest_examples(item, TABLE.foods)


def test_most_meals_of_realistic_days_get_two_or_more_examples() -> None:
    counts: list[int] = []
    for kg in (50.0, 60.0, 70.0, 80.0, 95.0):
        for sex in Sex:
            for goal in (MaintenanceGoal(), PerformanceGoal(), WeightLossGoal(500)):
                person = Person(sex, 30, 175, kg, ActivityLevel.ACTIVE)
                for training in ((), (TrainingSession(18 * 60, 60.0, 500.0, 60.0),)):
                    day = plan_day(DayInput(person, goal, MealTimes.default(), training))
                    counts += [len(suggest_examples(m, TABLE.foods)) for m in day.meals]

    assert sum(c >= 2 for c in counts) / len(counts) >= 0.9


def test_a_food_cannot_be_built_with_a_wrong_portion() -> None:
    banana = next(f for f in TABLE.foods if f.food_id == "banana")

    with pytest.raises(ValueError, match="step"):
        FoodPortion(banana, 125)  # not a multiple of 10
    with pytest.raises(ValueError, match="step"):
        FoodPortion(banana, 10_000)  # above the maximum


def test_foods_and_the_table_are_immutable_values() -> None:
    assert isinstance(TABLE, FoodTable)
    assert isinstance(TABLE.foods[0], Food)
    with pytest.raises(AttributeError):
        TABLE.foods[0].kcal_100g = 1.0  # type: ignore[misc]


def test_role_of_is_total_over_the_slots() -> None:
    for slot in MealSlot:
        assert isinstance(role_of((slot,)), MealRole)


# --- Nesting depth is limited before the JSON is decoded (D-F4B-06) -----------------------------


_MAX_SCAN_SECONDS = 1.0  # the only place that defines how long a size-limit input may take


def test_the_depth_check_is_linear_on_an_input_of_unterminated_strings() -> None:
    hostile = b'"' + b'\\"' * ((MAX_FOOD_TABLE_BYTES - 1) // 2)
    started = time.monotonic()

    with pytest.raises(FoodTableError):
        parse_food_table(hostile)

    assert time.monotonic() - started < _MAX_SCAN_SECONDS


@given(
    st.recursive(
        st.none() | st.booleans() | st.integers() | st.text(max_size=8),
        lambda inner: (
            st.lists(inner, max_size=3) | st.dictionaries(st.text(max_size=5), inner, max_size=3)
        ),
        max_leaves=15,
    )
)
def test_the_depth_check_agrees_with_the_nesting_of_valid_json(value: object) -> None:
    def depth_of(item: object) -> int:
        if isinstance(item, list):
            return 1 + max(map(depth_of, item), default=0)
        if isinstance(item, dict):
            return 1 + max(map(depth_of, item.values()), default=0)
        return 0

    data = json.dumps(value).encode("utf-8")

    with pytest.raises(FoodTableError) as refused:
        parse_food_table(data)
    assert ("nested" in str(refused.value)) == (depth_of(value) > MAX_FOOD_TABLE_DEPTH)


def _document_nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


@pytest.mark.parametrize("depth", [9, 50, 100_000])
def test_a_document_nested_deeper_than_the_limit_is_refused_before_decoding(depth: int) -> None:
    with pytest.raises(FoodTableError, match="nested"):
        parse_food_table(_document_nested(depth))


def test_brackets_inside_strings_do_not_count_as_nesting() -> None:
    document = raw_document()
    document["foods"][0]["name"] = "[[[[[[[[[[[[[[[[[[[[ \\" + '"' + "]]]]]]]]]]]]]]]]]]]]"

    table = parse_food_table(json.dumps(document).encode("utf-8"))

    assert len(table.foods) == len(TABLE.foods)


def test_the_shipped_table_is_well_inside_the_depth_limit() -> None:
    assert MAX_FOOD_TABLE_DEPTH >= 4
    assert parse_food_table(json.dumps(raw_document()).encode("utf-8")).foods == TABLE.foods
