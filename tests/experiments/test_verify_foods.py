# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tests of the pure logic of `experiments/verify_foods.py` (no network)."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

_PATH = Path(__file__).resolve().parents[2] / "experiments" / "verify_foods.py"
_spec = importlib.util.spec_from_file_location("verify_foods", _PATH)
assert _spec is not None
assert _spec.loader is not None
vf: Any = importlib.util.module_from_spec(_spec)
sys.modules["verify_foods"] = vf
_spec.loader.exec_module(vf)


def _nutrients(kcal: Any = 100, carbs: Any = 20, protein: Any = 5, fat: Any = 1) -> list[Any]:
    return [
        {"nutrientNumber": "208", "unitName": "KCAL", "value": kcal},
        {"nutrientNumber": "205", "unitName": "G", "value": carbs},
        {"nutrientNumber": "203", "unitName": "G", "value": protein},
        {"nutrientNumber": "204", "unitName": "G", "value": fat},
    ]


def _entry(fdc_id: int = 1, description: str = "Oats, rolled", nutrients: Any = None) -> Any:
    return {
        "fdcId": fdc_id,
        "description": description,
        "dataType": "SR Legacy",
        "foodNutrients": _nutrients() if nutrients is None else nutrients,
    }


def _food(fdc_id: int = 1) -> dict[str, Any]:
    return {
        "id": "oats",
        "name": "Rolled oats",
        "fdc_id": fdc_id,
        "kcal_100g": 100,
        "carbs_100g": 20.0,
        "protein_100g": 5.0,
        "fat_100g": 1.0,
    }


def test_valid_entry_is_parsed() -> None:
    foods, rejected = vf.parse_fdc_response([_entry()])
    assert rejected == {}
    assert foods[1].kcal == 100
    assert foods[1].data_type == "SR Legacy"


def test_energy_in_kj_is_not_taken_as_kcal() -> None:
    nutrients = _nutrients()
    nutrients[0]["unitName"] = "kJ"
    foods, rejected = vf.parse_fdc_response([_entry(nutrients=nutrients)])
    assert foods == {}
    assert "kcal" in rejected[1]


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), 5000, "12", True, None], ids=repr)
def test_hostile_nutrient_values_are_rejected(bad: Any) -> None:
    foods, rejected = vf.parse_fdc_response([_entry(nutrients=_nutrients(carbs=bad))])
    assert foods == {}
    assert "carbs" in rejected[1]


def test_missing_nutrients_are_reported() -> None:
    foods, rejected = vf.parse_fdc_response([_entry(nutrients=_nutrients()[:2])])
    assert foods == {}
    assert "fat" in rejected[1]
    assert "protein" in rejected[1]


@pytest.mark.parametrize(
    "payload", [{}, "text", 3, None, [{"x": 1}] * 101], ids=["dict", "str", "int", "none", "long"]
)
def test_wrong_top_level_raises(payload: Any) -> None:
    with pytest.raises(vf.FdcFormatError):
        vf.parse_fdc_response(payload)


@pytest.mark.parametrize(
    "entry",
    [
        3,
        None,
        {"fdcId": "1", "description": "x", "foodNutrients": []},
        {"fdcId": True, "description": "x", "foodNutrients": []},
        {"fdcId": 1, "description": 7, "foodNutrients": []},
        {"fdcId": 1, "description": "x", "foodNutrients": "no"},
        {"fdcId": 1, "description": "x", "foodNutrients": [None, 3, {"a": 1}]},
    ],
)
def test_hostile_entries_never_become_foods(entry: Any) -> None:
    foods, _ = vf.parse_fdc_response([entry])
    assert foods == {}


def test_long_description_is_truncated() -> None:
    foods, _ = vf.parse_fdc_response([_entry(description="a" * 10_000)])
    assert len(foods[1].description) == vf.MAX_DESCRIPTION_CHARS


def test_huge_body_is_refused_before_parsing() -> None:
    with pytest.raises(vf.FdcFormatError):
        vf.decode_json_bytes(b"[" + b" " * vf.MAX_RESPONSE_BYTES + b"]")


@pytest.mark.parametrize("raw", [b"\xff\xfe", b"{not json", b"[" * 100_000])
def test_invalid_json_is_a_format_error(raw: bytes) -> None:
    with pytest.raises(vf.FdcFormatError):
        vf.decode_json_bytes(raw)


def test_diff_pct_cases() -> None:
    assert vf.diff_pct(110, 100) == pytest.approx(10.0)
    assert vf.diff_pct(0, 0) == 0.0
    assert math.isinf(vf.diff_pct(1, 0))


def test_compare_flags_difference_missing_and_description() -> None:
    foods, rejected = vf.parse_fdc_response(
        [_entry(1, "Oats, rolled", _nutrients(kcal=200)), _entry(3, "Cheddar cheese")]
    )
    table = [_food(1), _food(2), {**_food(3), "name": "Rolled oats"}]
    rows = vf.compare_foods(table, foods, rejected)
    assert rows[0].over_threshold
    assert not rows[0].suspicious_description
    assert rows[1].problem == "id not returned by FDC"
    assert rows[2].suspicious_description
    report = vf.format_report(rows)
    assert "[OVER 10%]" in report
    assert "PROBLEM" in report
    assert "[SUSPICIOUS DESCRIPTION]" in report


def test_batches_are_at_most_20_unique_and_ordered() -> None:
    ids = [*range(43), 0, 1]
    result = vf.batches(ids)
    assert [len(b) for b in result] == [20, 20, 3]
    assert [i for b in result for i in b] == list(range(43))


def test_apply_rewrites_values_and_keeps_the_rest() -> None:
    document: dict[str, Any] = {
        "source": {"verified_on": None, "name": "x"},
        "foods": [_food(1), _food(2)],
    }
    foods, _ = vf.parse_fdc_response([_entry(1, nutrients=_nutrients(379.4, 67.74, 13.26, 6.5))])
    updated = vf.apply_fdc_values(document, foods, date(2026, 11, 7))
    assert updated["source"] == {"verified_on": "2026-11-07", "name": "x"}
    first = updated["foods"][0]
    assert (first["kcal_100g"], first["carbs_100g"], first["protein_100g"]) == (379, 67.7, 13.3)
    assert first["fdc_dataset"] == "SR Legacy"
    assert updated["foods"][1] == _food(2)
    assert [f["id"] for f in updated["foods"]] == ["oats", "oats"]
    assert document["source"]["verified_on"] is None  # input not mutated
    json.dumps(updated)
