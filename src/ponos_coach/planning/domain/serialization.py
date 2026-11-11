# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Canonical JSON of the plan's plain data: same value, same bytes.

Dataclasses become objects with a `_type` key, enums their value, dates ISO strings, sets sorted
lists, mappings objects with sorted keys, floats rounded to 3 decimals. Pure.
"""

from __future__ import annotations

import dataclasses
import json
import math
from collections.abc import Mapping
from datetime import date
from enum import Enum

_DECIMALS = 3


def _key(key: object) -> str:
    return str(key.value) if isinstance(key, Enum) else str(key)


def _number(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError("a non-finite number cannot be serialised")
    return round(value, _DECIMALS) + 0.0


def _collection(value: object) -> object | None:
    """Mappings, sets and sequences; `None` if the value is none of them."""
    if isinstance(value, Mapping):
        return {_key(k): _plain(v) for k, v in sorted(value.items(), key=lambda kv: _key(kv[0]))}
    if isinstance(value, frozenset | set):
        return sorted((_plain(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    if isinstance(value, tuple | list):
        return [_plain(v) for v in value]
    return None


def _plain(value: object) -> object:
    """The value as JSON-able data (recursive)."""
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        fields = {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
        return {"_type": type(value).__name__, **fields}
    collection = _collection(value)
    if collection is None:
        raise TypeError(f"cannot serialise {type(value).__name__}")
    return collection


def canonical_json(value: object) -> str:
    """The canonical JSON text of a plan or any of its parts."""
    return json.dumps(_plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
