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


@pytest.mark.parametrize(
    ("current", "reference", "tolerance", "expected"),
    [
        (0.4, 0.2, 0.5, False),  # 100% relative but 0.2 g: tiny value
        (1.0, 0.4, 0.5, True),  # over both
        (0.9, 0.4, 0.5, False),  # on the absolute limit: not over it
        (105, 100, 5.0, False),  # 5% relative
        (130, 100, 5.0, True),
        (104, 90, 5.0, True),  # 15.6% and 14 kcal
        (0.6, 0.0, 0.5, True),  # reference zero: infinite relative difference
        (0.3, 0.0, 0.5, False),
    ],
)
def test_a_difference_counts_only_over_both_the_relative_and_the_absolute_tolerance(
    current: float, reference: float, tolerance: float, expected: bool
) -> None:
    assert vf.is_significant_diff(current, reference, tolerance) is expected


def test_small_fat_differences_are_not_flagged_but_a_wrong_food_is() -> None:
    foods, rejected = vf.parse_fdc_response(
        [
            _entry(1, "Oats, rolled", _nutrients(fat=1.4)),
            _entry(2, "Oats, rolled", _nutrients(kcal=200)),
        ]
    )
    rows = vf.compare_foods([_food(1), _food(2)], foods, rejected)
    assert not rows[0].over_threshold  # fat 1.0 vs 1.4: 28% but 0.4 g
    assert rows[1].over_threshold


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
    # the second food has no FDC entry: the table as a whole is not verified
    assert updated["source"] == {"verified_on": None, "name": "x"}
    first = updated["foods"][0]
    assert (first["kcal_100g"], first["carbs_100g"], first["protein_100g"]) == (379, 67.7, 13.3)
    assert first["fdc_dataset"] == "SR Legacy"
    assert updated["foods"][1] == _food(2)
    assert [f["id"] for f in updated["foods"]] == ["oats", "oats"]
    assert document["source"]["verified_on"] is None  # input not mutated
    json.dumps(updated)


# --- Nutrient numbers as integers or strings --------------------------------------------------


def test_nutrient_numbers_given_as_integers_are_accepted() -> None:
    nutrients = [
        {"number": 208, "unitName": "kcal", "amount": 100},
        {"number": 205, "unitName": "g", "amount": 20},
        {"number": "203", "unitName": "g", "amount": 5},
        {"nutrientNumber": 204, "unitName": "g", "value": 1},
    ]

    foods, rejected = vf.parse_fdc_response([_entry(nutrients=nutrients)])

    assert rejected == {}
    assert (foods[1].kcal, foods[1].carbs, foods[1].protein, foods[1].fat) == (100, 20, 5, 1)


@pytest.mark.parametrize("number", [True, 208.0, None, [208], "0208"])
def test_nutrient_numbers_of_the_wrong_kind_are_not_taken(number: Any) -> None:
    nutrients = _nutrients()
    nutrients[0] = {"number": number, "unitName": "KCAL", "value": 100}

    foods, rejected = vf.parse_fdc_response([_entry(nutrients=nutrients)])

    assert foods == {}
    assert "kcal" in rejected[1]


# --- --apply: refused with problems, revalidated, verified only when everything is ------------

SHIPPED = json.loads(vf.FOODS_PATH.read_text(encoding="utf-8"))
TODAY = date(2026, 11, 7)


def _fdc_like_the_table(document: dict[str, Any]) -> Any:
    """FDC entries identical to the table's own values: nothing differs, nothing is suspicious."""
    return [
        {
            "fdcId": food["fdc_id"],
            "description": food["name"],
            "dataType": "SR Legacy",
            "foodNutrients": _nutrients(
                food["kcal_100g"], food["carbs_100g"], food["protein_100g"], food["fat_100g"]
            ),
        }
        for food in document["foods"]
    ]


def _compare(document: dict[str, Any], entries: list[Any]) -> tuple[Any, list[Any]]:
    batches = [entries[i : i + vf.MAX_ENTRIES] for i in range(0, len(entries), vf.MAX_ENTRIES)]
    found: dict[int, Any] = {}
    rejected: dict[int, str] = {}
    for batch in batches:
        ok, bad = vf.parse_fdc_response(batch)
        found.update(ok)
        rejected.update(bad)
    return found, vf.compare_foods(document["foods"], found, rejected)


def test_applying_a_table_without_problems_writes_it_verified_and_valid() -> None:
    fdc, rows = _compare(SHIPPED, _fdc_like_the_table(SHIPPED))

    text = vf.render_applied_document(SHIPPED, fdc, rows, TODAY)

    assert json.loads(text)["source"]["verified_on"] == "2026-11-07"
    assert text.endswith("\n")


@pytest.mark.parametrize(
    ("problem", "verified_after_force"),
    [("missing id", False), ("discarded", False), ("over 10%", True), ("suspicious", False)],
)
def test_applying_is_refused_when_a_food_has_a_problem_unless_forced(
    problem: str, verified_after_force: bool
) -> None:
    entries = _fdc_like_the_table(SHIPPED)
    if problem == "missing id":
        del entries[3]
    elif problem == "discarded":
        entries[3]["foodNutrients"] = _nutrients()[:2]
    elif problem == "over 10%":
        entries[3]["foodNutrients"] = _nutrients(
            kcal=SHIPPED["foods"][3]["kcal_100g"] * 1.2,
            carbs=SHIPPED["foods"][3]["carbs_100g"] * 1.2,
            protein=SHIPPED["foods"][3]["protein_100g"] * 1.2,
            fat=SHIPPED["foods"][3]["fat_100g"] * 1.2,
        )
    else:
        entries[3]["description"] = "Zzzzz qqqqq"
    fdc, rows = _compare(SHIPPED, entries)

    with pytest.raises(vf.ApplyRefusedError, match="--force"):
        vf.render_applied_document(SHIPPED, fdc, rows, TODAY)
    forced = json.loads(vf.render_applied_document(SHIPPED, fdc, rows, TODAY, force=True))

    # forced: verified only if every food now holds FDC's own values for an entry that fits it
    # (a difference over 10% is cured by the write itself; a missing or odd entry is not)
    assert (forced["source"]["verified_on"] == "2026-11-07") is verified_after_force


def test_the_updated_document_is_revalidated_with_the_parser_of_the_table() -> None:
    entries = _fdc_like_the_table(SHIPPED)
    entries[0]["foodNutrients"] = _nutrients(kcal=900, carbs=1, protein=1, fat=1)  # not Atwater
    fdc, rows = _compare(SHIPPED, entries)

    with pytest.raises(vf.ApplyRefusedError, match="not valid"):
        vf.render_applied_document(SHIPPED, fdc, rows, TODAY, force=True)
