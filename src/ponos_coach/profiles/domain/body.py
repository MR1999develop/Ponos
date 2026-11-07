# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Personal body data: height, weight, date of birth.

These are health data: they never appear in error messages or logs (the exceptions of this
module do not repeat the rejected value). Height and date of birth are fixed from
profile creation; weight has a series of entries with validity date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

HEIGHT_MIN_CM = 100
HEIGHT_MAX_CM = 250
WEIGHT_MIN_TENTHS_KG = 200  # 20,0 kg
WEIGHT_MAX_TENTHS_KG = 3000  # 300,0 kg
MIN_BIRTH_DATE = date(1900, 1, 1)
MIN_MEASUREMENT_DATE = date(1900, 1, 1)  # first validity date allowed for measurements
MIN_AGE_YEARS = 5  # project default: no profile for anyone under 5 years old
_WEIGHT_TEXT_MAX_CHARS = 16  # the length is checked before parsing the text
_TENTHS_PER_KG = 10
_ASCII_BLANKS = " \t\r\n"  # spaces at both ends; other spaces (even Unicode) are rejected
# Grammar allowed for the weight text: ASCII digits only, at most one decimal separator
# (comma or dot); the number of significant decimal digits is checked by `from_kg`.
_WEIGHT_TEXT = re.compile(r"\+?[0-9]+(?:[.,][0-9]*)?", re.ASCII)
_MIN_KG = Decimal(WEIGHT_MIN_TENTHS_KG) / _TENTHS_PER_KG
_MAX_KG = Decimal(WEIGHT_MAX_TENTHS_KG) / _TENTHS_PER_KG
_FEBRUARY = 2
_LEAP_DAY = 29


@dataclass(frozen=True, slots=True)
class Height:
    """Height in centimeters, integer, between 100 and 250."""

    cm: int

    def __post_init__(self) -> None:
        if not HEIGHT_MIN_CM <= self.cm <= HEIGHT_MAX_CM:
            raise ValueError(
                f"the height must be an integer between {HEIGHT_MIN_CM} and {HEIGHT_MAX_CM} cm"
            )


@dataclass(frozen=True, slots=True)
class Weight:
    """Weight in kilograms with one decimal digit, between 20.0 and 300.0 kg.

    It is an integer in tenths of a kilogram: no comparison goes through binary floating point.
    """

    tenths_kg: int

    def __post_init__(self) -> None:
        if not WEIGHT_MIN_TENTHS_KG <= self.tenths_kg <= WEIGHT_MAX_TENTHS_KG:
            raise ValueError("the weight must be between 20.0 and 300.0 kg")

    @classmethod
    def from_kg(cls, kg: Decimal) -> Weight:
        """Weight from a `Decimal` in kilograms, with at most one significant decimal digit.

        Raises:
            ValueError: non-finite value, with more than one decimal digit (`70.55`) or outside
                20.0-300.0 kg.
        """
        if not kg.is_finite():
            raise ValueError("the weight must be a number")
        if not _MIN_KG <= kg <= _MAX_KG:  # before multiplying: huge exponents overflow
            raise ValueError("the weight must be between 20.0 and 300.0 kg")
        tenths = kg * _TENTHS_PER_KG
        if tenths != tenths.to_integral_value():
            raise ValueError("the weight has at most one decimal digit")
        return cls(int(tenths))

    @classmethod
    def parse(cls, text: str) -> Weight:
        """Weight from user text: accepts comma or decimal point (`70,5` and `70.5`).

        ASCII digits only, an optional leading `+` sign and at most one decimal separator;
        (ASCII) spaces only at both ends. Exponents, `_`, non-ASCII or full-width digits,
        `NaN` and `Infinity` are rejected, never reinterpreted.

        Raises:
            ValueError: text too long, not written with this grammar, with more than one
                significant decimal digit or outside 20.0-300.0 kg.
        """
        if len(text) > _WEIGHT_TEXT_MAX_CHARS:
            raise ValueError("the weight must be a number")
        stripped = text.strip(_ASCII_BLANKS)
        if _WEIGHT_TEXT.fullmatch(stripped) is None:
            raise ValueError("the weight must be a number")
        try:
            kg = Decimal(stripped.replace(",", "."))
        except InvalidOperation as exc:  # unreachable: the grammar is a subset
            raise ValueError("the weight must be a number") from exc
        return cls.from_kg(kg)

    @property
    def kg(self) -> Decimal:
        """The weight in kilograms, exact (one decimal digit)."""
        return Decimal(self.tenths_kg) / _TENTHS_PER_KG


@dataclass(frozen=True, slots=True)
class WeightEntry:
    """Weight valid from a day (included) until the next entry."""

    valid_from: date
    weight: Weight


def _years_before(day: date, years: int) -> date:
    """The same day `years` years earlier (29 February becomes 28)."""
    if day.month == _FEBRUARY and day.day == _LEAP_DAY:
        return date(day.year - years, _FEBRUARY, _LEAP_DAY - 1)
    return day.replace(year=day.year - years)


@dataclass(frozen=True, slots=True)
class BirthDate:
    """Date of birth, not earlier than 1900-01-01.

    The upper limit (today minus 5 years) depends on the clock, which the domain does not read:
    it is computed by `BirthDate.latest(today)` and checked by `BirthDate.on`, to be used when the
    date
    is entered by the user. The constructor
    also serves to reread an already saved date, which does not become invalid with the passing of
    time.
    """

    value: date

    def __post_init__(self) -> None:
        if self.value < MIN_BIRTH_DATE:
            raise ValueError("the date of birth cannot be earlier than 1900-01-01")

    @staticmethod
    def latest(today: date) -> date:
        """The latest date of birth allowed today: `today` minus 5 years (29 February included:
        it becomes 28 February)."""
        return _years_before(today, MIN_AGE_YEARS)

    @classmethod
    def on(cls, value: date, today: date) -> BirthDate:
        """Date of birth entered today: from 1900-01-01 to `today` minus 5 years (inclusive).

        Raises:
            ValueError: date outside these limits.
        """
        if value > cls.latest(today):
            raise ValueError(f"the date of birth must be at least {MIN_AGE_YEARS} years ago")
        return cls(value)


@dataclass(frozen=True, slots=True)
class FixedProfileData:
    """Fixed data from profile creation: height and date of birth."""

    height: Height
    birth_date: BirthDate
