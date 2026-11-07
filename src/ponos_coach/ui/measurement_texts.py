# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Texts of "Edit profile" about validity periods and the values in force.

The module (`measurements_form`) gives categories and dates; here the sentences are chosen. Each
sentence is whole,
one per series and per case (closed or open period), so the translation does not compose fragments.
Weight and HR are health data: the texts serve only the window, never the logs.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.profiles.domain import Weight
from ponos_coach.profiles.measurements import ValidityPeriod
from ponos_coach.ui.measurement_history_model import weight_text
from ponos_coach.ui.measurements_form import EditField, FieldChange, MeasurementSeries

# Context "MeasurementTexts" written as a literal: lupdate does not follow aliases.


def date_text(day: date) -> str:
    """A date in the locale's short format."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def saved_period_text(series: MeasurementSeries, period: ValidityPeriod) -> str:
    """For how long what was just saved is valid ("from ... to ..." or "from ... onwards")."""
    first = date_text(period.valid_from)
    if period.valid_to is None:
        if series is MeasurementSeries.WEIGHT:
            return QCoreApplication.translate(
                "MeasurementTexts", "Weight valid from {first} onwards."
            ).format(first=first)
        return QCoreApplication.translate(
            "MeasurementTexts", "Heart rate valid from {first} onwards."
        ).format(first=first)
    last = date_text(period.valid_to)
    if series is MeasurementSeries.WEIGHT:
        return QCoreApplication.translate(
            "MeasurementTexts", "Weight valid from {first} to {last}."
        ).format(first=first, last=last)
    return QCoreApplication.translate(
        "MeasurementTexts", "Heart rate valid from {first} to {last}."
    ).format(first=first, last=last)


def superseded_text(series: MeasurementSeries, last_day: date) -> str:
    """Warning before saving: an already existing later measurement takes the place of the new
    one."""
    last = date_text(last_day)
    if series is MeasurementSeries.WEIGHT:
        return QCoreApplication.translate(
            "MeasurementTexts",
            "The new weight applies only until {last}: a later weight replaces it after that day.",
        ).format(last=last)
    return QCoreApplication.translate(
        "MeasurementTexts",
        "The new heart-rate values apply only until {last}: later values replace them after "
        "that day.",
    ).format(last=last)


def in_force_text(change: FieldChange) -> str:
    """Under a changed field: the value that applied that day before the change."""
    before = change.before
    if change.field is EditField.WEIGHT:
        if isinstance(before, Weight):
            return QCoreApplication.translate(
                "MeasurementTexts", "In force on this day: {value} kg"
            ).format(value=weight_text(before))
        return QCoreApplication.translate("MeasurementTexts", "No weight is recorded for this day.")
    return QCoreApplication.translate(
        "MeasurementTexts", "In force on this day: {value} bpm"
    ).format(value=QLocale().toString(before if isinstance(before, int) else 0))
