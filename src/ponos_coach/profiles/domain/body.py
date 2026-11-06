# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dati personali del corpo: altezza, peso, data di nascita.

Sono dati di salute: non compaiono mai nei messaggi d'errore né nei log (le eccezioni di questo
modulo non ripetono il valore rifiutato). L'altezza e la data di nascita sono fisse dalla
creazione del profilo; il peso ha una serie di voci con data di validità.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

HEIGHT_MIN_CM = 100
HEIGHT_MAX_CM = 250
WEIGHT_MIN_TENTHS_KG = 200  # 20,0 kg
WEIGHT_MAX_TENTHS_KG = 3000  # 300,0 kg
MIN_BIRTH_DATE = date(1900, 1, 1)
MIN_AGE_YEARS = 5  # default del progetto: nessun profilo per chi ha meno di 5 anni
_WEIGHT_TEXT_MAX_CHARS = 16  # la lunghezza si controlla prima di analizzare il testo
_TENTHS_PER_KG = 10
_MIN_KG = Decimal(WEIGHT_MIN_TENTHS_KG) / _TENTHS_PER_KG
_MAX_KG = Decimal(WEIGHT_MAX_TENTHS_KG) / _TENTHS_PER_KG
_FEBRUARY = 2
_LEAP_DAY = 29


@dataclass(frozen=True, slots=True)
class Height:
    """Altezza in centimetri, intera, tra 100 e 250."""

    cm: int

    def __post_init__(self) -> None:
        if not HEIGHT_MIN_CM <= self.cm <= HEIGHT_MAX_CM:
            raise ValueError(
                f"l'altezza deve essere un intero tra {HEIGHT_MIN_CM} e {HEIGHT_MAX_CM} cm"
            )


@dataclass(frozen=True, slots=True)
class Weight:
    """Peso in chilogrammi con una cifra decimale, tra 20,0 e 300,0 kg.

    È un intero in decimi di chilogrammo: nessun confronto passa da virgola mobile binaria.
    """

    tenths_kg: int

    def __post_init__(self) -> None:
        if not WEIGHT_MIN_TENTHS_KG <= self.tenths_kg <= WEIGHT_MAX_TENTHS_KG:
            raise ValueError("il peso deve stare tra 20,0 e 300,0 kg")

    @classmethod
    def from_kg(cls, kg: Decimal) -> Weight:
        """Peso da un `Decimal` in chilogrammi, con al più una cifra decimale significativa.

        Raises:
            ValueError: valore non finito, con più di una cifra decimale (`70.55`) o fuori
                da 20,0-300,0 kg.
        """
        if not kg.is_finite():
            raise ValueError("il peso deve essere un numero")
        if not _MIN_KG <= kg <= _MAX_KG:  # prima di moltiplicare: gli esponenti enormi traboccano
            raise ValueError("il peso deve stare tra 20,0 e 300,0 kg")
        tenths = kg * _TENTHS_PER_KG
        if tenths != tenths.to_integral_value():
            raise ValueError("il peso ha al più una cifra decimale")
        return cls(int(tenths))

    @classmethod
    def parse(cls, text: str) -> Weight:
        """Peso da testo dell'utente: accetta virgola o punto decimale (`70,5` e `70.5`).

        Raises:
            ValueError: testo troppo lungo, non numerico, con più di una cifra decimale o
                fuori da 20,0-300,0 kg.
        """
        if len(text) > _WEIGHT_TEXT_MAX_CHARS:
            raise ValueError("il peso deve essere un numero")
        try:
            kg = Decimal(text.strip().replace(",", "."))
        except InvalidOperation as exc:
            raise ValueError("il peso deve essere un numero") from exc
        return cls.from_kg(kg)

    @property
    def kg(self) -> Decimal:
        """Il peso in chilogrammi, esatto (una cifra decimale)."""
        return Decimal(self.tenths_kg) / _TENTHS_PER_KG


@dataclass(frozen=True, slots=True)
class WeightEntry:
    """Peso valido da un giorno (incluso) fino alla voce successiva."""

    valid_from: date
    weight: Weight


def _years_before(day: date, years: int) -> date:
    """Lo stesso giorno `years` anni prima (il 29 febbraio diventa il 28)."""
    if day.month == _FEBRUARY and day.day == _LEAP_DAY:
        return date(day.year - years, _FEBRUARY, _LEAP_DAY - 1)
    return day.replace(year=day.year - years)


@dataclass(frozen=True, slots=True)
class BirthDate:
    """Data di nascita, non anteriore al 1900-01-01.

    Il limite superiore (oggi meno 5 anni) dipende dall'orologio, che il dominio non legge:
    lo verifica `BirthDate.on`, da usare quando la data la inserisce l'utente. Il costruttore
    serve anche a rileggere una data già salvata, che non diventa invalida con il passare del
    tempo.
    """

    value: date

    def __post_init__(self) -> None:
        if self.value < MIN_BIRTH_DATE:
            raise ValueError("la data di nascita non può essere anteriore al 1900-01-01")

    @classmethod
    def on(cls, value: date, today: date) -> BirthDate:
        """Data di nascita inserita oggi: da 1900-01-01 a `today` meno 5 anni (incluso).

        Raises:
            ValueError: data fuori da questi limiti.
        """
        if value > _years_before(today, MIN_AGE_YEARS):
            raise ValueError(f"la data di nascita deve essere di almeno {MIN_AGE_YEARS} anni fa")
        return cls(value)


@dataclass(frozen=True, slots=True)
class FixedProfileData:
    """Dati fissi dalla creazione del profilo: altezza e data di nascita."""

    height: Height
    birth_date: BirthDate
