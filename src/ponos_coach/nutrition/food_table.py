# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The curated food table shipped with the app (`foods.json`) and its parser.

The file is versioned with the code and packaged as a resource; reading it is the job of the
Composition Root (once, at start-up), which hands the bytes to `parse_food_table`. The parser
turns them into domain types at the boundary: a table that does not parse is an error, never
repaired. Size and nesting depth are checked before the JSON decoder runs.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Final

from ponos_coach.nutrition.domain import Food, FoodGroup, MealRole

FOOD_TABLE_RESOURCE: Final = "foods.json"
MAX_FOOD_TABLE_BYTES: Final = 256 * 1024
MAX_FOOD_TABLE_DEPTH: Final = 8  # the real table nests 4 levels (document, foods, food, roles)
# A string, or one bracket: enough to follow the nesting without decoding anything. The closing
# quote is optional so that an unterminated string consumes the rest once (a scan that restarted
# at every stray quote would be quadratic); valid JSON has no unterminated string.
_TOKEN: Final = re.compile(rb'"(?:[^"\\]|\\.)*"?|[\[\]{}]')
FOOD_TABLE_FORMAT: Final = 2  # 2: CIQUAL codes and names (1 was USDA FoodData Central ids)
_MIN_FOODS: Final = 40
_MAX_FOODS: Final = 200


class FoodTableError(Exception):
    """The food table cannot be read or is not valid."""


@dataclass(frozen=True, slots=True)
class FoodSource:
    """Where the food numbers come from.

    `version` names the release of the source table; `verified_on` is the day somebody checked
    every entry against the source, None while that has not been done.
    """

    name: str
    version: str
    url: str
    license: str
    verified_on: date | None


@dataclass(frozen=True, slots=True)
class FoodTable:
    """The foods and their source; the foods are sorted by `food_id` and the ids are unique."""

    source: FoodSource
    foods: tuple[Food, ...]


def _check_depth(data: bytes) -> None:
    """Refuses a document nested deeper than `MAX_FOOD_TABLE_DEPTH` before it is decoded (the
    decoder would exhaust the stack on a deep one)."""
    depth = 0
    for token in _TOKEN.finditer(data):
        if token.group() in (b"[", b"{"):
            depth += 1
            if depth > MAX_FOOD_TABLE_DEPTH:
                raise FoodTableError("the food table is nested too deeply")
        elif token.group() in (b"]", b"}"):
            depth -= 1


def _require_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise FoodTableError("the food table has an object where it should not")
    return value


def _text(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise FoodTableError(f"missing text field {key}")
    return value


def _number(entry: dict[str, object], key: str) -> float:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise FoodTableError(f"missing numeric field {key}")
    return float(value)


def _whole(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise FoodTableError(f"missing integer field {key}")
    return value


def _food(raw: object) -> Food:
    entry = _require_mapping(raw)
    roles = entry.get("roles")
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise FoodTableError("a food has a list of roles")
    if not _text(entry, "ciqual_name").strip():  # the entry's original name: required, unused
        raise FoodTableError("a food has the original name of its CIQUAL entry")
    if "ciqual_note" in entry:
        _text(entry, "ciqual_note")
    try:
        return Food(
            _text(entry, "id"),
            _text(entry, "name"),
            _whole(entry, "ciqual_code"),
            FoodGroup(_text(entry, "group")),
            frozenset(MealRole(r) for r in roles),
            _number(entry, "kcal_100g"),
            _number(entry, "carbs_100g"),
            _number(entry, "protein_100g"),
            _number(entry, "fat_100g"),
            _whole(entry, "portion_step_g"),
            _whole(entry, "max_portion_g"),
        )
    except ValueError as exc:  # also the unknown group or role names
        raise FoodTableError("a food entry is not valid") from exc


def _source(raw: object) -> FoodSource:
    entry = _require_mapping(raw)
    verified = entry.get("verified_on")
    try:
        day = None if verified is None else date.fromisoformat(str(verified))
    except ValueError as exc:
        raise FoodTableError("the verification date is not a date") from exc
    return FoodSource(
        _text(entry, "name"),
        _text(entry, "version"),
        _text(entry, "url"),
        _text(entry, "license"),
        day,
    )


def parse_food_table(data: bytes) -> FoodTable:
    """Food table from the bytes of the file.

    Raises:
        FoodTableError: too large, nested too deeply, not JSON, wrong format version, an invalid
            entry, repeated ids or too few or too many foods.
    """
    if len(data) > MAX_FOOD_TABLE_BYTES:
        raise FoodTableError("the food table is too large")
    _check_depth(data)
    try:
        document = _require_mapping(json.loads(data))
    except ValueError as exc:
        raise FoodTableError("the food table is not valid JSON") from exc
    if document.get("format") != FOOD_TABLE_FORMAT:
        raise FoodTableError("unknown food table format")
    raw_foods = document.get("foods")
    if not isinstance(raw_foods, list) or not _MIN_FOODS <= len(raw_foods) <= _MAX_FOODS:
        raise FoodTableError("the food table has too few or too many foods")
    foods = tuple(sorted((_food(item) for item in raw_foods), key=lambda f: f.food_id))
    if len({f.food_id for f in foods}) != len(foods):
        raise FoodTableError("food ids must be unique")
    return FoodTable(_source(document.get("source")), foods)
