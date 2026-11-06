# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Testi delle attività: il dominio dà categorie, qui si sceglie come dirle in parole."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale

from ponos_coach.activities.domain import FileFormat, Sport
from ponos_coach.load.domain import LoadSource

# Contesto "Activities" scritto come letterale: lupdate non segue gli alias.

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_METERS_PER_KM = 1000.0
_DISTANCE_DECIMALS = 2


def sport_text(sport: Sport) -> str:
    """Nome dello sport per l'utente."""
    texts = {
        Sport.RUNNING: QCoreApplication.translate("Activities", "Running"),
        Sport.TRAIL: QCoreApplication.translate("Activities", "Trail running"),
        Sport.CYCLING: QCoreApplication.translate("Activities", "Cycling"),
        Sport.STRENGTH: QCoreApplication.translate("Activities", "Strength"),
        Sport.HIKING: QCoreApplication.translate("Activities", "Hiking"),
        Sport.WALKING: QCoreApplication.translate("Activities", "Walking"),
        Sport.OTHER: QCoreApplication.translate("Activities", "Other"),
    }
    return texts[sport]


def load_source_text(source: LoadSource) -> str:
    """Da dove viene il carico: dalla FC misurata o stimato dalla durata."""
    if source is LoadSource.HEART_RATE:
        return QCoreApplication.translate("Activities", "Heart rate")
    return QCoreApplication.translate("Activities", "Estimated")


def file_format_text(file_format: FileFormat) -> str:
    """Formato del file originale (sigla, uguale in ogni lingua)."""
    return file_format.value.upper()


def distance_km_text(distance_m: float | None) -> str:
    """Distanza in chilometri nel formato del luogo; vuota se il dato manca (seduta indoor)."""
    if distance_m is None:
        return ""
    return QLocale().toString(distance_m / _METERS_PER_KM, "f", _DISTANCE_DECIMALS)


def ascent_m_text(ascent_m: float | None) -> str:
    """Dislivello positivo in metri interi; vuoto se il dato manca."""
    return "" if ascent_m is None else QLocale().toString(round(ascent_m))


def duration_text(seconds: float) -> str:
    """Durata come `h:mm:ss`, arrotondata al secondo."""
    total = round(seconds)
    hours, rest = divmod(total, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    return f"{hours}:{minutes:02d}:{secs:02d}"
