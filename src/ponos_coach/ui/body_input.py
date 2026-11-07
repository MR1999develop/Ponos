# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Altezza, peso e data di nascita scritti dall'utente: lettura e messaggi dei moduli.

Altezza, peso e data di nascita sono dati di salute: il valore scritto non compare mai in un
messaggio, in un log né in un'eccezione. Il dominio decide cosa è valido (`Height`, `Weight`,
`BirthDate.on`); qui si traduce il testo in quei tipi, o in una categoria di problema.
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

_HEIGHT_TEXT_MAX_CHARS = 3  # la lunghezza si controlla prima di leggere il numero
_TENTHS_PER_KG = 10


class BodyProblem(Enum):
    """Che cosa non va in un valore del corpo scritto dall'utente."""

    HEIGHT_EMPTY = "height_empty"
    HEIGHT_INVALID = "height_invalid"
    WEIGHT_EMPTY = "weight_empty"
    WEIGHT_INVALID = "weight_invalid"
    BIRTH_EMPTY = "birth_empty"
    BIRTH_INVALID = "birth_invalid"


def read_height(text: str) -> Height | BodyProblem:
    """Altezza da un testo di cifre (centimetri interi); mai un valore a metà."""
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
    """Peso da un testo con virgola o punto decimale."""
    if not text.strip():
        return BodyProblem.WEIGHT_EMPTY
    try:
        return Weight.parse(text)
    except ValueError:
        return BodyProblem.WEIGHT_INVALID


def read_birth_date(value: date | None, today: date) -> BirthDate | BodyProblem:
    """Data di nascita inserita oggi (almeno cinque anni fa, non prima del 1900)."""
    if value is None:
        return BodyProblem.BIRTH_EMPTY
    try:
        return BirthDate.on(value, today)
    except ValueError:
        return BodyProblem.BIRTH_INVALID


def latest_birth_date(today: date) -> date:
    """L'ultima data di nascita ammessa oggi, secondo il dominio (per il limite del selettore)."""
    return BirthDate.latest(today)


def body_problem_text(problem: BodyProblem) -> str:
    """Messaggio sotto il campo: cosa scrivere, senza ripetere ciò che era scritto."""
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
