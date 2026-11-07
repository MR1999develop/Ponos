# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Height, weight and birth date typed by the user: reading and form messages.

Height, weight and birth date are health data: the typed value never appears in a
message, a log or an exception. The domain decides what is valid (`Height`, `Weight`,
`BirthDate.on`); here the text is turned into those types, or into a problem category.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from PySide6.QtCore import QCoreApplication

from ponos_coach.profiles.domain import (
    HEIGHT_MAX_CM,
    HEIGHT_MIN_CM,
    MIN_AGE_YEARS,
    MIN_BIRTH_DATE,
    WEIGHT_MAX_TENTHS_KG,
    WEIGHT_MIN_TENTHS_KG,
    BirthDate,
    Height,
    Weight,
)

_HEIGHT_TEXT_MAX_CHARS = 3  # the length is checked before reading the number
_TENTHS_PER_KG = 10


class BodyProblem(Enum):
    """What is wrong with a body value typed by the user."""

    HEIGHT_EMPTY = "height_empty"
    HEIGHT_INVALID = "height_invalid"
    WEIGHT_EMPTY = "weight_empty"
    WEIGHT_INVALID = "weight_invalid"
    BIRTH_EMPTY = "birth_empty"
    BIRTH_INVALID = "birth_invalid"


def read_height(text: str) -> Height | BodyProblem:
    """Height from a text of digits (whole centimetres); never a half value."""
    stripped = text.strip()
    if not stripped:
        return BodyProblem.HEIGHT_EMPTY
    if len(stripped) > _HEIGHT_TEXT_MAX_CHARS or not (stripped.isascii() and stripped.isdigit()):
        return BodyProblem.HEIGHT_INVALID
    try:
        return Height(int(stripped))
    except ValueError:
        return BodyProblem.HEIGHT_INVALID


def read_weight(text: str) -> Weight | BodyProblem:
    """Weight from a text with a comma or a decimal point."""
    if not text.strip():
        return BodyProblem.WEIGHT_EMPTY
    try:
        return Weight.parse(text)
    except ValueError:
        return BodyProblem.WEIGHT_INVALID


def read_birth_date(value: date | None, today: date) -> BirthDate | BodyProblem:
    """Birth date entered today (at least five years ago, not before 1900)."""
    if value is None:
        return BodyProblem.BIRTH_EMPTY
    try:
        return BirthDate.on(value, today)
    except ValueError:
        return BodyProblem.BIRTH_INVALID


def latest_birth_date(today: date) -> date:
    """The latest birth date allowed today, per the domain (for the picker limit)."""
    return BirthDate.latest(today)


def body_problem_text(problem: BodyProblem) -> str:
    """Message under the field: what to type, without repeating what was typed."""
    texts = {
        BodyProblem.HEIGHT_EMPTY: QCoreApplication.translate(
            "BodyInput", "Enter the height in centimetres."
        ),
        BodyProblem.HEIGHT_INVALID: QCoreApplication.translate(
            "BodyInput",
            "Enter the height as a whole number of centimetres, from {low} to {high}.",
        ).format(low=HEIGHT_MIN_CM, high=HEIGHT_MAX_CM),
        BodyProblem.WEIGHT_EMPTY: QCoreApplication.translate(
            "BodyInput", "Enter the weight in kilograms."
        ),
        BodyProblem.WEIGHT_INVALID: QCoreApplication.translate(
            "BodyInput",
            "Enter the weight in kilograms with at most one decimal, from {low} to {high}.",
        ).format(
            low=WEIGHT_MIN_TENTHS_KG // _TENTHS_PER_KG, high=WEIGHT_MAX_TENTHS_KG // _TENTHS_PER_KG
        ),
        BodyProblem.BIRTH_EMPTY: QCoreApplication.translate(
            "BodyInput", "Enter the date of birth."
        ),
        BodyProblem.BIRTH_INVALID: QCoreApplication.translate(
            "BodyInput",
            "The date of birth must be at least {years} years ago and not before {first}.",
        ).format(years=MIN_AGE_YEARS, first=MIN_BIRTH_DATE.isoformat()),
    }
    return texts[problem]
