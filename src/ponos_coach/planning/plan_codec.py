# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Reads a saved plan back: the canonical JSON of `Plan.to_json()` becomes a `Plan` again.

The text comes from the database, so it is input like any other: size first, then checksum, then
syntax, then the shape the types declare (every dataclass with exactly its fields, every enum with
a known value, every date in the form `isoformat()` writes). The domain types check their own
invariants as they are built. Whatever fails is a `PlanDecodingError`; nothing is repaired and the
text is never repeated in the error.

The decoder follows the annotations of the types, so a new field of the plan is read back without
touching this file. Floats come back as the 3-decimal numbers that were written: reading and
writing again gives the same text.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import types
import typing
from collections.abc import Callable, Mapping
from datetime import date
from enum import Enum
from functools import cache
from typing import Final

from ponos_coach.planning.domain import Plan, PlanDay

MAX_PLAN_JSON_CHARS: Final = 16 * 1024 * 1024  # a full-year plan is well under 2 MiB
MAX_PLAN_DAY_JSON_CHARS: Final = 64 * 1024
_TYPE_KEY: Final = "_type"


class PlanDecodingError(Exception):
    """The saved text is not a valid plan (too big, checksum, syntax or content)."""


class _MismatchError(Exception):
    """The value is not of the expected type (a union tries the next member)."""


def digest(text: str) -> str:
    """SHA-256 of the text (UTF-8) in hexadecimal: the checksum saved with a plan."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def decode_plan(text: str, sha256: str) -> Plan:
    """The plan in `text`, whose checksum must be `sha256`.

    Raises:
        PlanDecodingError: text too big, checksum different, invalid JSON or content.
    """
    return _decode_text(text, sha256, MAX_PLAN_JSON_CHARS, Plan)


def decode_plan_day(text: str, sha256: str) -> PlanDay:
    """The day in `text` (the canonical JSON of a `PlanDay`), whose checksum must be `sha256`.

    Raises:
        PlanDecodingError: text too big, checksum different, invalid JSON or content.
    """
    return _decode_text(text, sha256, MAX_PLAN_DAY_JSON_CHARS, PlanDay)


def _decode_text[T](text: str, sha256: str, max_chars: int, expected: type[T]) -> T:
    if len(text) > max_chars:
        raise PlanDecodingError("the saved plan is too big")
    if digest(text) != sha256:
        raise PlanDecodingError("the checksum of the saved plan does not match")
    try:
        decoded = _decode(json.loads(text, parse_constant=_reject_constant), expected)
    except (ValueError, TypeError, OverflowError, RecursionError, _MismatchError):
        # The text is not kept in the chain of causes: it comes from outside.
        raise PlanDecodingError("the saved plan is not valid") from None
    if not isinstance(decoded, expected):
        raise PlanDecodingError("the saved plan is not valid")
    return decoded


def _reject_constant(_name: str) -> object:
    raise ValueError("NaN and infinities are not JSON")


# --- Decoding by type ---------------------------------------------------------------------------
@cache
def _fields_of(cls: type) -> tuple[tuple[str, object], ...]:
    """The constructor fields of a dataclass with their resolved types."""
    hints = typing.get_type_hints(cls)
    return tuple((f.name, hints[f.name]) for f in dataclasses.fields(cls) if f.init)


def _unalias(hint: object) -> object:
    while isinstance(hint, typing.TypeAliasType):
        hint = hint.__value__
    return hint


def _is_dataclass_type(hint: object) -> bool:
    return isinstance(hint, type) and dataclasses.is_dataclass(hint)


def _decode(value: object, hint: object) -> object:
    hint = _unalias(hint)
    origin = typing.get_origin(hint)
    if origin is not None:
        return _decode_generic(value, origin, typing.get_args(hint))
    scalar = _SCALARS.get(hint)
    if scalar is not None:
        return scalar(value)
    if isinstance(hint, type) and issubclass(hint, Enum):
        return _decode_enum(value, hint)
    if _is_dataclass_type(hint):
        return _decode_dataclass(value, typing.cast("type", hint))
    raise TypeError(f"no decoder for {hint!r}")


def _decode_generic(value: object, origin: object, args: tuple[object, ...]) -> object:
    if origin is types.UnionType or origin is typing.Union:
        return _decode_union(value, args)
    if origin is tuple:
        return _decode_tuple(value, args)
    if origin is frozenset:
        return frozenset(_decode(item, args[0]) for item in _as_list(value))
    if isinstance(origin, type) and issubclass(origin, Mapping):
        return _decode_mapping(value, args[0], args[1])
    raise TypeError(f"no decoder for {origin!r}")


def _decode_union(value: object, members: tuple[object, ...]) -> object:
    if value is None:
        if type(None) in members:
            return None
        raise _MismatchError
    if isinstance(value, dict) and _TYPE_KEY in value:
        return _decode_tagged(value, members)
    for member in members:
        if member is type(None):
            continue
        try:
            return _decode(value, member)
        except _MismatchError:
            continue
    raise _MismatchError


def _decode_tagged(value: dict[str, object], members: tuple[object, ...]) -> object:
    """The member of the union that is the dataclass named by the value's `_type`."""
    for member in map(_unalias, members):
        if _is_dataclass_type(member) and typing.cast("type", member).__name__ == value[_TYPE_KEY]:
            return _decode_dataclass(value, typing.cast("type", member))
    raise _MismatchError


def _decode_tuple(value: object, args: tuple[object, ...]) -> tuple[object, ...]:
    items = _as_list(value)
    if len(args) == 2 and args[1] is Ellipsis:
        return tuple(_decode(item, args[0]) for item in items)
    if len(items) != len(args):
        raise _MismatchError
    return tuple(_decode(item, hint) for item, hint in zip(items, args, strict=True))


def _decode_mapping(value: object, key_hint: object, value_hint: object) -> dict[object, object]:
    if not isinstance(value, dict):
        raise _MismatchError
    return {_decode_key(key, key_hint): _decode(item, value_hint) for key, item in value.items()}


def _decode_key(key: str, hint: object) -> object:
    if isinstance(hint, type) and issubclass(hint, Enum):
        return _decode_enum(key, hint)
    if hint is str:
        return key
    raise TypeError(f"no decoder for keys of type {hint!r}")


def _decode_dataclass(value: object, cls: type) -> object:
    fields = _fields_of(cls)
    if not isinstance(value, dict) or value.get(_TYPE_KEY) != cls.__name__:
        raise _MismatchError
    if set(value) != {_TYPE_KEY, *(name for name, _ in fields)}:
        raise _MismatchError
    return cls(**{name: _decode(value[name], hint) for name, hint in fields})


def _decode_enum(value: object, cls: type[Enum]) -> Enum:
    try:
        return cls(value)
    except ValueError:
        raise _MismatchError from None


def _decode_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _MismatchError
    number = float(value)
    if not math.isfinite(number):
        raise _MismatchError
    return number


def _decode_date(value: object) -> date:
    if not isinstance(value, str):
        raise _MismatchError
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _MismatchError from None
    if parsed.isoformat() != value:  # only the form that `isoformat()` writes
        raise _MismatchError
    return parsed


def _exact[T](value: object, kind: type[T]) -> T:
    if type(value) is not kind:
        raise _MismatchError
    return value


_SCALARS: Final[dict[object, Callable[[object], object]]] = {
    bool: lambda value: _exact(value, bool),
    int: lambda value: _exact(value, int),
    str: lambda value: _exact(value, str),
    float: _decode_float,
    date: _decode_date,
}


def _as_list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise _MismatchError
    return value
