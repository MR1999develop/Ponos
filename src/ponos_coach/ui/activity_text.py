# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity texts: the domain gives categories, here we choose how to word them."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import LoadSource

# Context "Activities" written as a literal: lupdate does not follow aliases.

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_METERS_PER_KM = 1000.0
_DISTANCE_DECIMALS = 2


def sport_text(sport: Sport) -> str:
    """Sport name for the user."""
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
    """Where the load comes from: measured HR or estimated from duration."""
    if source is LoadSource.HEART_RATE:
        return QCoreApplication.translate("Activities", "Heart rate")
    return QCoreApplication.translate("Activities", "Estimated")


def distance_km_text(distance_m: float | None) -> str:
    """Distance in kilometres in the locale's format; empty if the datum is missing (indoor
    session)."""
    if distance_m is None:
        return ""
    return QLocale().toString(distance_m / _METERS_PER_KM, "f", _DISTANCE_DECIMALS)


def pace_text(s_per_km: float | None) -> str:
    """A pace as `m:ss` per kilometre, to the whole second; empty if there is none."""
    if s_per_km is None:
        return ""
    minutes, seconds = divmod(round(s_per_km), _SECONDS_PER_MINUTE)
    return f"{minutes}:{seconds:02d}"


def ascent_m_text(ascent_m: float | None) -> str:
    """Positive elevation gain in whole metres; empty if the datum is missing."""
    return "" if ascent_m is None else QLocale().toString(round(ascent_m))


def duration_text(seconds: float) -> str:
    """Duration as `h:mm:ss`, rounded to the second."""
    total = round(seconds)
    hours, rest = divmod(total, _SECONDS_PER_HOUR)
    minutes, secs = divmod(rest, _SECONDS_PER_MINUTE)
    return f"{hours}:{minutes:02d}:{secs:02d}"
