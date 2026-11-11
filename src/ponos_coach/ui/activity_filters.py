# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Filters of the activity list: what the user typed, the criteria they make and the shared state.

The table and the map use one `SharedFilterState`: when the criteria change both show the same
activities (the data thread filters once, with the domain's `ActivityFilter`). The state is
remembered between runs in `QSettings`, in a canonical text form (dates `YYYY-MM-DD`, numbers with
a point) that does not depend on the locale; what the bar shows is formatted for the locale. The
tolerant parsing of the numbers is the domain's; dates are parsed here (ISO or the locale's short
format). Presentation logic with no widgets: testable without a window.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Final

from PySide6.QtCore import QCoreApplication, QDate, QLocale, QObject, QSettings, Signal

from ponos_coach.activities.domain import (
    ActivityFilter,
    FilterInputError,
    FilterInputReason,
    NumericRange,
    Sport,
    parse_amount,
    parse_distance_km,
    parse_duration,
)

_ISO_DATE: Final = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_MAX_DATE_CHARS: Final = 32
_SECONDS_PER_MINUTE: Final = 60.0
_METERS_PER_KM: Final = 1000.0
_KEY_PREFIX: Final = "filters/"
_SPORT_SEPARATOR: Final = ","


class FilterField(Enum):
    """The text fields of the filter bar, in display order."""

    DATE_FROM = "dateFrom"
    DATE_TO = "dateTo"
    DURATION_MIN = "durationMin"
    DURATION_MAX = "durationMax"
    DISTANCE_MIN = "distanceMin"
    DISTANCE_MAX = "distanceMax"
    ASCENT_MIN = "ascentMin"
    ASCENT_MAX = "ascentMax"


class FieldProblem(Enum):
    """Why a field cannot be used. The value is stable."""

    NOT_A_DATE = "not_a_date"
    NOT_A_NUMBER = "not_a_number"
    NEGATIVE = "negative"
    TOO_LARGE = "too_large"


_PROBLEM_OF_REASON: Final = {
    FilterInputReason.NOT_A_NUMBER: FieldProblem.NOT_A_NUMBER,
    FilterInputReason.NEGATIVE: FieldProblem.NEGATIVE,
    FilterInputReason.TOO_LARGE: FieldProblem.TOO_LARGE,
}


@dataclass(frozen=True, slots=True)
class FilterTexts:
    """What the filter bar holds: the text of every field and the chosen sports (empty = all)."""

    date_from: str = ""
    date_to: str = ""
    duration_min: str = ""
    duration_max: str = ""
    distance_min: str = ""
    distance_max: str = ""
    ascent_min: str = ""
    ascent_max: str = ""
    sports: frozenset[Sport] = frozenset()

    def text_of(self, field: FilterField) -> str:
        """The text of one field."""
        return _TEXT_ATTRIBUTE[field](self)

    def with_text(self, field: FilterField, text: str) -> FilterTexts:
        """A copy with one field changed."""
        values = {f: self.text_of(f) for f in FilterField}
        values[field] = text
        return FilterTexts.from_fields(values, self.sports)

    @classmethod
    def from_fields(
        cls, values: Mapping[FilterField, str], sports: frozenset[Sport] = frozenset()
    ) -> FilterTexts:
        """Texts from a value per field (a missing field is empty)."""
        return cls(
            values.get(FilterField.DATE_FROM, ""),
            values.get(FilterField.DATE_TO, ""),
            values.get(FilterField.DURATION_MIN, ""),
            values.get(FilterField.DURATION_MAX, ""),
            values.get(FilterField.DISTANCE_MIN, ""),
            values.get(FilterField.DISTANCE_MAX, ""),
            values.get(FilterField.ASCENT_MIN, ""),
            values.get(FilterField.ASCENT_MAX, ""),
            sports,
        )


_TEXT_ATTRIBUTE: Final[Mapping[FilterField, Callable[[FilterTexts], str]]] = {
    FilterField.DATE_FROM: lambda t: t.date_from,
    FilterField.DATE_TO: lambda t: t.date_to,
    FilterField.DURATION_MIN: lambda t: t.duration_min,
    FilterField.DURATION_MAX: lambda t: t.duration_max,
    FilterField.DISTANCE_MIN: lambda t: t.distance_min,
    FilterField.DISTANCE_MAX: lambda t: t.distance_max,
    FilterField.ASCENT_MIN: lambda t: t.ascent_min,
    FilterField.ASCENT_MAX: lambda t: t.ascent_max,
}


@dataclass(frozen=True, slots=True)
class FilterParse:
    """The outcome of reading the fields: the criteria, or the problem of each wrong field."""

    criteria: ActivityFilter | None
    problems: Mapping[FilterField, FieldProblem]


def parse_date_text(text: str) -> date | None:
    """A day typed as `YYYY-MM-DD` or in the locale's short format; empty means no limit.

    Raises:
        ValueError: not a date (the text is not repeated).
    """
    body = text.strip()
    if not body:
        return None
    if len(body) > _MAX_DATE_CHARS:
        raise ValueError("not a date")
    iso = _ISO_DATE.fullmatch(body)
    if iso is not None:
        year, month, day = (int(group) for group in iso.groups())
        return date(year, month, day)  # ValueError for 31 February
    parsed = QLocale().toDate(body, QLocale.FormatType.ShortFormat)
    if not parsed.isValid():
        raise ValueError("not a date")
    return date(parsed.year(), parsed.month(), parsed.day())


def _read[T](
    field: FilterField, read: Callable[[], T], problems: dict[FilterField, FieldProblem]
) -> T | None:
    try:
        return read()
    except FilterInputError as error:
        problems[field] = _PROBLEM_OF_REASON[error.reason]
    except ValueError:
        problems[field] = FieldProblem.NOT_A_DATE
    return None


def parse_filter(texts: FilterTexts) -> FilterParse:
    """The criteria for the fields, or the problem of every wrong field (never a partial filter).

    Empty fields are no limit; a minimum above the maximum is swapped; dates likewise.
    """
    problems: dict[FilterField, FieldProblem] = {}

    def number(field: FilterField, read: Callable[[str], float | None]) -> float | None:
        return _read(field, lambda: read(texts.text_of(field)), problems)

    def day(field: FilterField) -> date | None:
        return _read(field, lambda: parse_date_text(texts.text_of(field)), problems)

    first, last = day(FilterField.DATE_FROM), day(FilterField.DATE_TO)
    duration = (
        number(FilterField.DURATION_MIN, parse_duration),
        number(FilterField.DURATION_MAX, parse_duration),
    )
    distance = (
        number(FilterField.DISTANCE_MIN, parse_distance_km),
        number(FilterField.DISTANCE_MAX, parse_distance_km),
    )
    ascent = (
        number(FilterField.ASCENT_MIN, parse_amount),
        number(FilterField.ASCENT_MAX, parse_amount),
    )
    if problems:
        return FilterParse(None, problems)
    criteria = ActivityFilter(
        date_from=first,
        date_to=last,
        duration_s=NumericRange.of(*duration),
        distance_m=NumericRange.of(*distance),
        ascent_m=NumericRange.of(*ascent),
        sports=texts.sports,
    )
    return FilterParse(criteria, {})


# --- Texts of a criteria, canonical (saved) and for the locale (shown) ----------------------


def _number_text(value: float | None, scale: float, format_number: Callable[[float], str]) -> str:
    return "" if value is None else format_number(value / scale)


def _canonical_number(value: float) -> str:
    return f"{value:g}"


def _locale_number(value: float) -> str:
    return QLocale().toString(
        value, "g", QLocale.FloatingPointPrecisionOption.FloatingPointShortest
    )


def _iso_day(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _locale_day(value: date | None) -> str:
    if value is None:
        return ""
    return QLocale().toString(
        QDate(value.year, value.month, value.day), QLocale.FormatType.ShortFormat
    )


def _texts_of(
    criteria: ActivityFilter, day: Callable[[date | None], str], number: Callable[[float], str]
) -> FilterTexts:
    def pair(r: NumericRange, scale: float) -> tuple[str, str]:
        return _number_text(r.minimum, scale, number), _number_text(r.maximum, scale, number)

    duration, distance, ascent = (
        pair(criteria.duration_s, _SECONDS_PER_MINUTE),
        pair(criteria.distance_m, _METERS_PER_KM),
        pair(criteria.ascent_m, 1.0),
    )
    return FilterTexts(
        day(criteria.date_from),
        day(criteria.date_to),
        *duration,
        *distance,
        *ascent,
        sports=criteria.sports,
    )


def canonical_texts(criteria: ActivityFilter) -> FilterTexts:
    """The criteria as text independent of the locale (what is saved)."""
    return _texts_of(criteria, _iso_day, _canonical_number)


def display_texts(criteria: ActivityFilter) -> FilterTexts:
    """The criteria as text for the locale (what the bar shows)."""
    return _texts_of(criteria, _locale_day, _locale_number)


# --- Shared, remembered state ---------------------------------------------------------------


class SharedFilterState(QObject):
    """The one filter that the table and the map share, remembered between runs."""

    changed = Signal(object)  # ActivityFilter

    def __init__(self, settings: QSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._criteria = self._restored()

    @property
    def criteria(self) -> ActivityFilter:
        """The filter in force (empty = every activity)."""
        return self._criteria

    def displayed(self) -> FilterTexts:
        """What the bar shows for the filter in force."""
        return display_texts(self._criteria)

    def apply(self, texts: FilterTexts) -> FilterParse:
        """Read the fields; if they are valid they become the filter (and are remembered)."""
        outcome = parse_filter(texts)
        if outcome.criteria is not None:
            self._set(outcome.criteria)
        return outcome

    def clear(self) -> None:
        """Back to no filter."""
        self._set(ActivityFilter())

    def _set(self, criteria: ActivityFilter) -> None:
        if criteria == self._criteria:
            return
        self._criteria = criteria
        self._save(criteria)
        self.changed.emit(criteria)

    def _save(self, criteria: ActivityFilter) -> None:
        texts = canonical_texts(criteria)
        for field in FilterField:
            self._settings.setValue(_KEY_PREFIX + field.value, texts.text_of(field))
        names = sorted(sport.value for sport in criteria.sports)
        self._settings.setValue(_KEY_PREFIX + "sports", _SPORT_SEPARATOR.join(names))

    def _restored(self) -> ActivityFilter:
        """The saved filter; anything unreadable means no filter (never an error)."""
        values: dict[FilterField, str] = {}
        for field in FilterField:
            raw = self._settings.value(_KEY_PREFIX + field.value, "")
            values[field] = raw if isinstance(raw, str) else ""
        texts = FilterTexts.from_fields(values, self._restored_sports())
        return parse_filter(texts).criteria or ActivityFilter()

    def _restored_sports(self) -> frozenset[Sport]:
        raw = self._settings.value(_KEY_PREFIX + "sports", "")
        if not isinstance(raw, str):
            return frozenset()
        known = {sport.value: sport for sport in Sport}
        return frozenset(known[name] for name in raw.split(_SPORT_SEPARATOR) if name in known)


# --- Texts for the user ---------------------------------------------------------------------


def field_label(field: FilterField) -> str:
    """The name of a field in messages, with its unit."""
    labels = {
        FilterField.DATE_FROM: QCoreApplication.translate("Filters", "From date"),
        FilterField.DATE_TO: QCoreApplication.translate("Filters", "To date"),
        FilterField.DURATION_MIN: QCoreApplication.translate(
            "Filters", "Minimum duration (minutes)"
        ),
        FilterField.DURATION_MAX: QCoreApplication.translate(
            "Filters", "Maximum duration (minutes)"
        ),
        FilterField.DISTANCE_MIN: QCoreApplication.translate("Filters", "Minimum distance (km)"),
        FilterField.DISTANCE_MAX: QCoreApplication.translate("Filters", "Maximum distance (km)"),
        FilterField.ASCENT_MIN: QCoreApplication.translate("Filters", "Minimum ascent (m)"),
        FilterField.ASCENT_MAX: QCoreApplication.translate("Filters", "Maximum ascent (m)"),
    }
    return labels[field]


def problem_text(field: FilterField, problem: FieldProblem) -> str:
    """What is wrong with a field and what to type instead (the typed text is not repeated)."""
    name = field_label(field)
    # {field}: the name of the field; {example}: a date in the locale's short format.
    if problem is FieldProblem.NOT_A_DATE:
        example = QLocale().toString(QDate(2026, 3, 5), QLocale.FormatType.ShortFormat)
        return QCoreApplication.translate(
            "Filters", "{field}: not a date. Write it like 2026-03-05 or like {example}."
        ).format(field=name, example=example)
    if problem is FieldProblem.NEGATIVE:
        return QCoreApplication.translate(
            "Filters", "{field}: must not be negative. Leave it empty for no limit."
        ).format(field=name)
    if problem is FieldProblem.TOO_LARGE:
        return QCoreApplication.translate(
            "Filters", "{field}: too large. Leave it empty for no limit."
        ).format(field=name)
    return QCoreApplication.translate(
        "Filters", "{field}: not a number. Write a number such as 10.5 (a duration also as 1:30)."
    ).format(field=name)
