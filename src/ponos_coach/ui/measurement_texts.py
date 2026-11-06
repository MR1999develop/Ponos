# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Testi di "Modifica profilo" sui periodi di validità e sui valori in vigore.

Il modulo (`measurements_form`) dà categorie e date; qui si scelgono le frasi. Ogni frase è intera,
una per serie e per caso (periodo chiuso o aperto), così la traduzione non compone frammenti.
Peso e FC sono dati di salute: i testi servono solo alla finestra, mai ai log.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QCoreApplication, QDate, QLocale

from ponos_coach.profiles.domain import Weight
from ponos_coach.profiles.measurements import ValidityPeriod
from ponos_coach.ui.measurement_history_model import weight_text
from ponos_coach.ui.measurements_form import EditField, FieldChange, MeasurementSeries

# Contesto "MeasurementTexts" scritto come letterale: lupdate non segue gli alias.


def date_text(day: date) -> str:
    """Una data nel formato breve del luogo."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def saved_period_text(series: MeasurementSeries, period: ValidityPeriod) -> str:
    """Per quanto vale ciò che si è appena salvato ("dal ... al ..." o "dal ... in poi")."""
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
    """Avviso prima di salvare: una misura successiva già esistente prende il posto della nuova."""
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
    """Sotto un campo cambiato: il valore che valeva quel giorno prima della modifica."""
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
