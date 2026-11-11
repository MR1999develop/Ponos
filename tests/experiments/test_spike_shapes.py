# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tests of `shape_of` of the Garmin spike: structure without values."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_MODULE_PATH = Path(__file__).resolve().parents[2] / "experiments" / "spike_shapes.py"
_spec = importlib.util.spec_from_file_location("spike_shapes", _MODULE_PATH)
assert _spec is not None
assert _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
shape_of: Any = _module.shape_of

SAMPLE: dict[str, Any] = {
    "activityId": 987654321012,
    "activityName": "Corsa di prova segreta",
    "startTimeLocal": "2031-02-03 04:05:06",
    "distance": 12345.678,
    "averageHR": 151.0,
    "elevationLoss": -42,
    "hasPolyline": True,
    "ownerEmail": None,
    "activityType": {"typeKey": "running", "parentTypeId": 17},
    "splits": [{"lap": 1, "label": "primo"}, {"lap": 2, "label": "secondo"}],
    "vuoto": [],
    "atleta.prova@example.invalid": "token-segreto-abc",
    "1234567890123": {"x": 1},
}


def test_scalar_types_and_magnitudes() -> None:
    assert shape_of(None) == {"type": "null"}
    assert shape_of(True) == {"type": "boolean"}
    assert shape_of(0) == {"type": "integer", "magnitude": "0"}
    assert shape_of(12345) == {"type": "integer", "magnitude": "10^4"}
    assert shape_of(0.05) == {"type": "number", "magnitude": "10^-2"}
    assert shape_of(-7) == {"type": "integer", "magnitude": "10^0", "negative": True}
    assert shape_of("abcd") == {"type": "string", "length": 4}


def test_array_reports_length_and_first_element_only() -> None:
    assert shape_of([]) == {"type": "array", "length": 0, "first": None}
    assert shape_of([{"a": 1}, {"b": 2}]) == {
        "type": "array",
        "length": 2,
        "first": {"type": "object", "keys": {"a": {"type": "integer", "magnitude": "10^0"}}},
    }


def test_dynamic_keys_are_replaced_by_placeholders() -> None:
    shape = shape_of(SAMPLE)
    keys = shape["keys"]
    assert "<email-key-1>" in keys
    assert "<id-key-1>" in keys
    assert "activityType" in keys
    assert not any("@" in key or "1234567890123" in key for key in keys)


def test_depth_is_bounded() -> None:
    value: Any = 1
    for _ in range(40):
        value = {"k": value}
    assert "truncated" in json.dumps(shape_of(value))


def test_no_original_value_appears_in_output() -> None:
    output = json.dumps(shape_of(SAMPLE))
    forbidden = [
        "987654321012",
        "Corsa di prova segreta",
        "2031-02-03",
        "12345.678",
        "151.0",
        "running",
        "primo",
        "secondo",
        "atleta.prova@example.invalid",
        "token-segreto-abc",
        "example.invalid",
        "1234567890123",
    ]
    for text in forbidden:
        assert text not in output
