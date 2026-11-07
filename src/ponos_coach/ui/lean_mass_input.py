# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The measured lean mass typed by the user: reading and form messages.

Lean mass is health data: the typed value never appears in a message, a log or an exception. The
domain decides what is valid (`LeanMass`); here the text is turned into that type, or into a
problem category. Like the weight, it accepts a comma or a decimal point.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from enum import Enum

from PySide6.QtCore import QCoreApplication

from ponos_coach.nutrition.domain import LeanMass, defaults

_TEXT_MAX_CHARS = 16  # the length is checked before reading the number
_ASCII_BLANKS = " \t\r\n"
# ASCII digits only, at most one decimal separator (comma or dot).
_TEXT = re.compile(r"\+?[0-9]+(?:[.,][0-9]*)?", re.ASCII)
_TENTHS_PER_KG = 10


class LeanMassProblem(Enum):
    """What is wrong with a lean mass typed by the user."""

    EMPTY = "empty"
    INVALID = "invalid"


def read_lean_mass(text: str) -> LeanMass | LeanMassProblem:
    """The lean mass from a text with a comma or a decimal point (`58,5` and `58.5`)."""
    stripped = text.strip(_ASCII_BLANKS)
    if not stripped:
        return LeanMassProblem.EMPTY
    if len(text) > _TEXT_MAX_CHARS or _TEXT.fullmatch(stripped) is None:
        return LeanMassProblem.INVALID
    try:
        return LeanMass.from_kg(Decimal(stripped.replace(",", ".")))
    except (InvalidOperation, ValueError):
        return LeanMassProblem.INVALID


def lean_mass_problem_text(problem: LeanMassProblem) -> str:
    """Message under the field: what to type, without repeating what was typed."""
    if problem is LeanMassProblem.EMPTY:
        return QCoreApplication.translate("LeanMass", "Enter the lean mass in kilograms.")
    # {low} and {high}: the least and the greatest lean mass, in kilograms, as numbers.
    return QCoreApplication.translate(
        "LeanMass",
        "Enter the lean mass in kilograms with at most one decimal, from {low} to {high}.",
    ).format(
        low=defaults.MIN_LEAN_MASS_TENTHS_KG // _TENTHS_PER_KG,
        high=defaults.MAX_LEAN_MASS_TENTHS_KG // _TENTHS_PER_KG,
    )
