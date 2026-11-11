# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Anonymous structure of a JSON response, to write realistic fixtures.

`shape_of(value)` returns a description that contains only: key names,
JSON type of each value, length of strings and arrays, order of
magnitude of numbers. No original value. Pure function: no I/O.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_DEPTH = 12
MAX_KEYS = 300
MAX_KEY_LENGTH = 48

# A "dynamic" key (email, id, date, long hexadecimal) may contain real data.
_EMAIL_KEY = re.compile(r"@")
_DIGITS_KEY = re.compile(r"\d{4,}")
_HEX_KEY = re.compile(r"[0-9a-fA-F-]{16,}")


def _key_placeholder(key: str) -> str | None:
    """Placeholder for a key that might contain real data, or None."""
    if _EMAIL_KEY.search(key):
        return "email-key"
    if _DIGITS_KEY.search(key) or _HEX_KEY.fullmatch(key):
        return "id-key"
    if len(key) > MAX_KEY_LENGTH:
        return "long-key"
    return None


def _magnitude(number: float) -> str:
    """Order of magnitude (`10^k`) of the absolute value; `0` for zero."""
    if number == 0:
        return "0"
    if not math.isfinite(number):
        return "non-finite"
    return f"10^{math.floor(math.log10(abs(number)))}"


def _shape_of_object(value: dict[Any, Any], depth: int) -> dict[str, Any]:
    keys: dict[str, Any] = {}
    counters: dict[str, int] = {}
    for position, (key, item) in enumerate(value.items()):
        if position >= MAX_KEYS:
            keys["<altre-chiavi-omesse>"] = {"count": len(value) - MAX_KEYS}
            break
        text = str(key)
        placeholder = _key_placeholder(text)
        if placeholder is not None:
            counters[placeholder] = counters.get(placeholder, 0) + 1
            text = f"<{placeholder}-{counters[placeholder]}>"
        keys[text] = shape_of(item, depth + 1)
    return {"type": "object", "keys": keys}


def _shape_of_scalar(value: object) -> dict[str, Any] | None:
    """Shape of null, booleans, numbers and strings; None if it is not a scalar."""
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, (int, float)):
        kind = "integer" if isinstance(value, int) else "number"
        shape: dict[str, Any] = {"type": kind, "magnitude": _magnitude(value)}
        if value < 0:
            shape["negative"] = True
        return shape
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    return None


def shape_of(value: object, depth: int = 0) -> dict[str, Any]:
    """Describes the structure of `value` without ever reporting its values.

    Object: `{"type": "object", "keys": {name: shape}}`; array: type,
    length and shape of the first element; string: length; number: order
    of magnitude (and `negative` if negative); dynamic keys replaced by
    placeholders; beyond `MAX_DEPTH` levels `{"type": "truncated"}`.
    """
    if depth > MAX_DEPTH:
        return {"type": "truncated"}
    scalar = _shape_of_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, dict):
        return _shape_of_object(value, depth)
    if isinstance(value, (list, tuple)):
        first = shape_of(value[0], depth + 1) if value else None
        return {"type": "array", "length": len(value), "first": first}
    return {"type": f"other:{type(value).__name__}"}
