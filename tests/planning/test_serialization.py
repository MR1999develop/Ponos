# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Canonical JSON: same value, same bytes, and nothing the plan cannot hold."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from enum import Enum

import pytest

from ponos_coach.planning.domain.serialization import canonical_json


class Colour(Enum):
    RED = "red"


@dataclass(frozen=True)
class Point:
    x: float
    y: int


def test_keys_are_sorted_and_there_are_no_spaces() -> None:
    assert canonical_json({"b": 1, "a": [1, 2]}) == '{"a":[1,2],"b":1}'


def test_floats_are_rounded_to_three_decimals_and_negative_zero_is_zero() -> None:
    assert canonical_json([1.23456, -0.0, 2.0, -0.0004]) == "[1.235,0.0,2.0,0.0]"


def test_dataclasses_carry_their_type_and_enums_their_value() -> None:
    assert canonical_json(Point(1.5, 2)) == '{"_type":"Point","x":1.5,"y":2}'
    assert canonical_json({Colour.RED: Colour.RED}) == '{"red":"red"}'


def test_dates_are_iso_and_sets_are_sorted() -> None:
    assert canonical_json(date(2026, 3, 2)) == '"2026-03-02"'
    assert canonical_json(frozenset({3, 1, 2})) == "[1,2,3]"
    assert canonical_json(frozenset({"b", "a"})) == '["a","b"]'


def test_equal_sets_in_any_construction_order_give_the_same_text() -> None:
    assert canonical_json({Colour.RED, 1}) == canonical_json({1, Colour.RED})


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_numbers_are_refused(value: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json(value)


def test_unknown_types_are_refused() -> None:
    with pytest.raises(TypeError, match="object"):
        canonical_json(object())
