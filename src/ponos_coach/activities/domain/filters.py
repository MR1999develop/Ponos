# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Filtri dell'elenco delle attività: tipi validi per costruzione e input tolleranti.

Un solo `ActivityFilter` serve la tabella e la mappa. Un filtro vuoto lascia passare tutto;
minimo e massimo sono facoltativi; se invertiti si scambiano. Le unità del dominio sono quelle
dell'indice (secondi, metri); chi mostra il filtro converte (minuti, chilometri).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, tzinfo
from enum import Enum

from ponos_coach.activities.domain.record import ActivityRecord
from ponos_coach.activities.domain.sport import Sport

_UNICODE_MINUS = chr(0x2212)
_MAX_INPUT_CHARS = 32  # la lunghezza si controlla prima di ogni espressione regolare
_NUMBER = re.compile(r"(\d{1,9})(?:[.,](\d{1,6}))?")
_CLOCK = re.compile(r"(\d{1,6}):([0-5]?\d)(?::([0-5]?\d))?")
_SECONDS_PER_MINUTE = 60.0
_SECONDS_PER_HOUR = 3600.0
_METERS_PER_KM = 1000.0
MAX_FILTER_VALUE = 1_000_000_000.0  # tetto di qualunque limite numerico (finito e sensato)


class FilterInputReason(Enum):
    """Perché un testo non è un limite valido. Il valore è stabile."""

    NOT_A_NUMBER = "not_a_number"
    NEGATIVE = "negative"
    TOO_LARGE = "too_large"


class FilterInputError(ValueError):
    """Il testo di un limite non è valido; `reason` dice perché (il testo non si ripete)."""

    def __init__(self, reason: FilterInputReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


def _number(text: str) -> float:
    """Numero non negativo con virgola o punto decimale (nessun separatore delle migliaia)."""
    body = text.strip()
    if len(body) > _MAX_INPUT_CHARS:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    if body.startswith(("-", _UNICODE_MINUS)):
        raise FilterInputError(FilterInputReason.NEGATIVE)
    match = _NUMBER.fullmatch(body)
    if match is None:
        raise FilterInputError(FilterInputReason.NOT_A_NUMBER)
    return float(f"{match.group(1)}.{match.group(2) or '0'}")


def parse_amount(text: str, scale: float = 1.0) -> float | None:
    """Quantità testuale → valore moltiplicato per la scala; testo vuoto = nessun limite.

    Con `scale = 1000` "10,5" (chilometri) dà 10500.0 metri.

    Raises:
        FilterInputError: testo non numerico, negativo o troppo grande.
    """
    if not text.strip():
        return None
    value = _number(text) * scale
    if value > MAX_FILTER_VALUE:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    return value


def parse_distance_km(text: str) -> float | None:
    """Chilometri testuali ("10", "10,5", "10.5") → metri; vuoto = nessun limite.

    Raises:
        FilterInputError: testo non numerico, negativo o troppo grande.
    """
    return parse_amount(text, _METERS_PER_KM)


def parse_duration(text: str) -> float | None:
    """Durata testuale → secondi; vuoto = nessun limite.

    "90" e "90,5" sono minuti; "1:30" è un'ora e 30 minuti; "1:30:15" ore, minuti e secondi.
    Minuti e secondi dopo i due punti stanno in 0-59.

    Raises:
        FilterInputError: testo non valido, negativo o troppo grande.
    """
    body = text.strip()
    if not body:
        return None
    clock = _CLOCK.fullmatch(body) if len(body) <= _MAX_INPUT_CHARS else None
    if clock is None:
        return parse_amount(body, _SECONDS_PER_MINUTE)
    hours, minutes, seconds = clock.group(1), clock.group(2), clock.group(3) or "0"
    value = int(hours) * _SECONDS_PER_HOUR + int(minutes) * _SECONDS_PER_MINUTE + int(seconds)
    if value > MAX_FILTER_VALUE:
        raise FilterInputError(FilterInputReason.TOO_LARGE)
    return float(value)


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Intervallo chiuso `[minimum, maximum]` con estremi facoltativi (`None` = senza limite).

    Estremi finiti, non negativi e al più `MAX_FILTER_VALUE`; se `minimum > maximum` la
    costruzione tramite `of` li scambia (il costruttore diretto invece li rifiuta).
    """

    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self) -> None:
        for bound in (self.minimum, self.maximum):
            if bound is not None and not (
                math.isfinite(bound) and 0.0 <= bound <= MAX_FILTER_VALUE
            ):
                raise ValueError("i limiti devono essere finiti e non negativi")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("il minimo supera il massimo")

    @classmethod
    def of(cls, first: float | None, second: float | None) -> NumericRange:
        """Intervallo da due estremi in qualunque ordine: se invertiti si scambiano."""
        if first is not None and second is not None and first > second:
            return cls(second, first)
        return cls(first, second)

    @property
    def is_open(self) -> bool:
        """Senza nessun limite."""
        return self.minimum is None and self.maximum is None

    def accepts(self, value: float | None) -> bool:
        """Se il valore sta nei limiti. Un valore ignoto passa solo se non ci sono limiti."""
        if self.is_open:
            return True
        if value is None:
            return False
        low_ok = self.minimum is None or value >= self.minimum
        return low_ok and (self.maximum is None or value <= self.maximum)


@dataclass(frozen=True, slots=True)
class ActivityFilter:
    """Criteri di ricerca sulle attività; tutti facoltativi e combinati con "e".

    `date_from` e `date_to` sono giorni inclusi (nel fuso passato a `matches`); se invertiti
    si scambiano. `duration_s` riguarda il tempo in movimento; `distance_m` e `ascent_m`
    (dislivello positivo) riguardano il riepilogo: un'attività che non li dichiara non passa
    un filtro che li limita. `sports` vuoto = tutti gli sport.
    """

    date_from: date | None = None
    date_to: date | None = None
    duration_s: NumericRange = NumericRange()
    distance_m: NumericRange = NumericRange()
    ascent_m: NumericRange = NumericRange()
    sports: frozenset[Sport] = frozenset()

    def __post_init__(self) -> None:
        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            first, second = self.date_to, self.date_from
            object.__setattr__(self, "date_from", first)
            object.__setattr__(self, "date_to", second)

    @property
    def is_empty(self) -> bool:
        """Se il filtro lascia passare qualunque attività."""
        return (
            self.date_from is None
            and self.date_to is None
            and self.duration_s.is_open
            and self.distance_m.is_open
            and self.ascent_m.is_open
            and not self.sports
        )

    def matches(self, record: ActivityRecord, tz: tzinfo = UTC) -> bool:
        """Se l'attività passa tutti i criteri."""
        day = record.start.astimezone(tz).date()
        return (
            (self.date_from is None or day >= self.date_from)
            and (self.date_to is None or day <= self.date_to)
            and (not self.sports or record.sport in self.sports)
            and self.duration_s.accepts(record.timer_s)
            and self.distance_m.accepts(record.summary.distance_m)
            and self.ascent_m.accepts(record.summary.ascent_m)
        )


def filter_activities(
    records: Iterable[ActivityRecord], criteria: ActivityFilter, tz: tzinfo = UTC
) -> list[ActivityRecord]:
    """Le attività che passano il filtro, nello stesso ordine. Filtro vuoto = tutte."""
    return [r for r in records if criteria.matches(r, tz)]
