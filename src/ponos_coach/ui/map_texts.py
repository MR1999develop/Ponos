# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Texts of the map view: summaries, notices with their remedy, legends and reasons.

The domain gives categories (a problem, an absence reason); the wording is here. Numbers and dates
follow the locale. Context "Map" written as a literal: lupdate does not follow aliases.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale

from ponos_coach.activities.domain import AreaTotals
from ponos_coach.load.activity_profile import ZoneAbsence
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.maps.domain import MapBackground
from ponos_coach.maps.ports import TileProblem
from ponos_coach.ui.errors import ErrorMessage, exclusion_reason_text
from ponos_coach.ui.load_figures import zone_label

_ZONE_COUNT = 5


def _number(value: float, decimals: int = 0) -> str:
    return QLocale().toString(value, "f", decimals)


# --- Summary of the area --------------------------------------------------------------------


def area_count_text(count: int) -> str:
    """How many filtered activities start in the area."""
    return QCoreApplication.translate("Map", "Activities in this area: %n", "", count)


def area_totals_text(totals: AreaTotals) -> str:
    """Distance, ascent and moving time of those activities."""
    # {distance}: kilometres; {ascent}: metres of positive ascent; {hours}: moving time in hours.
    return QCoreApplication.translate(
        "Map", "{distance} km  |  {ascent} m ascent  |  {hours} h moving"
    ).format(
        distance=_number(totals.distance_km, 1),
        ascent=_number(totals.ascent_m),
        hours=_number(totals.moving_hours, 1),
    )


def without_position_text(count: int) -> str:
    """Filtered activities that have no start position (indoor, strength): not on the map."""
    return QCoreApplication.translate(
        "Map", "Activities without a position (not on the map): %n", "", count
    )


def tracks_omitted_text(count: int) -> str:
    """Older tracks not drawn because too many are in view."""
    return QCoreApplication.translate(
        "Map", "Older tracks not drawn: %n. Zoom in to see more tracks.", "", count
    )


def zoom_for_tracks_text() -> str:
    """Why no tracks are drawn at a low zoom."""
    return QCoreApplication.translate(
        "Map",
        "Select an activity to see its track. Circles with a number group the starts of "
        "activities.",
    )


def empty_area_text() -> str:
    """No filtered activity starts in the visible area."""
    return QCoreApplication.translate(
        "Map", "No activity in this area. Move or zoom the map, or change the filters."
    )


def no_activities_text() -> str:
    """The profile has no activity (or none passes the filters)."""
    return QCoreApplication.translate("Map", "No activity matches the filters.")


# --- Notice about the map background --------------------------------------------------------


def tile_notice(problem: TileProblem, background: MapBackground) -> ErrorMessage:
    """What is wrong with the map background and what to do; tracks and list work anyway.

    The title starts with the name of the background (OpenStreetMap, OpenTopoMap, CyclOSM), so
    it is clear which server the notice is about.
    """
    message = _tile_notice(problem)
    # {background}: name of the map background; {problem}: what is wrong with it.
    title = QCoreApplication.translate("Map", "{background}: {problem}").format(
        background=background.label, problem=message.title
    )
    return ErrorMessage(title, message.what, message.remedy)


def _tile_notice(problem: TileProblem) -> ErrorMessage:
    if problem is TileProblem.REFUSED:
        return ErrorMessage(
            QCoreApplication.translate("Map", "The map server refuses the requests"),
            QCoreApplication.translate(
                "Map", "The map shows a plain background; tiles already seen still appear."
            ),
            QCoreApplication.translate(
                "Map",
                "Wait a few minutes and move the map again. Tracks, filters and the list work "
                "without it.",
            ),
        )
    if problem is TileProblem.NETWORK_UNAVAILABLE:
        return ErrorMessage(
            QCoreApplication.translate("Map", "The map background cannot be loaded"),
            QCoreApplication.translate(
                "Map", "There is no network, or the map server does not answer."
            ),
            QCoreApplication.translate(
                "Map", "Check the connection. The map tries again by itself when you move it."
            ),
        )
    if problem is TileProblem.UNTRUSTED_SERVER:
        return ErrorMessage(
            QCoreApplication.translate("Map", "The map server's certificate is not valid"),
            QCoreApplication.translate(
                "Map",
                "Its identity cannot be verified (for example, the certificate has expired), so "
                "nothing is loaded from it; tiles already seen still appear.",
            ),
            QCoreApplication.translate(
                "Map",
                "Choose another background, or try again later: only the server's owner can fix "
                "it. Tracks, filters and the list work without it.",
            ),
        )
    return ErrorMessage(
        QCoreApplication.translate("Map", "The map background cannot be shown"),
        QCoreApplication.translate("Map", "The map server sent something the map cannot use."),
        QCoreApplication.translate(
            "Map", "Try again later. Tracks, filters and the list are not affected."
        ),
    )


def notice_line(message: ErrorMessage) -> str:
    """The notice on one wrapped line: what is wrong, then what to do."""
    # {title}: what happened; {what}: the effect; {remedy}: what to do.
    return QCoreApplication.translate("Map", "{title}. {what} {remedy}").format(
        title=message.title.rstrip("."), what=message.what, remedy=message.remedy
    )


# --- Legends and the activity detail --------------------------------------------------------


def color_by_sport_text() -> str:
    """Name of the track colouring by sport."""
    return QCoreApplication.translate("Map", "By sport")


def color_by_zone_text() -> str:
    """Name of the track colouring by heart-rate zone."""
    return QCoreApplication.translate("Map", "By heart-rate zone")


def color_mode_label() -> str:
    """Label of the colouring choice."""
    return QCoreApplication.translate("Map", "Colour the track")


def zone_legend_text(index: int, starts_bpm: tuple[float, float, float, float] | None) -> str:
    """A zone's name and, if the thresholds are known, its heart-rate range."""
    name = zone_label(index)
    if starts_bpm is None:
        return name
    low = None if index == 0 else starts_bpm[index - 1]
    high = None if index == _ZONE_COUNT - 1 else starts_bpm[index]
    if low is None and high is not None:
        bounds = QCoreApplication.translate("Map", "below {high} bpm").format(high=_number(high))
    elif high is None and low is not None:
        bounds = QCoreApplication.translate("Map", "from {low} bpm").format(low=_number(low))
    else:
        bounds = QCoreApplication.translate("Map", "{low} to {high} bpm").format(
            low=_number(low or 0), high=_number(high or 0)
        )
    return f"{name}  ({bounds})"


def no_zone_legend_text() -> str:
    """Legend entry of the stretches without a usable heart-rate sample."""
    return QCoreApplication.translate("Map", "No usable heart rate (grey, dashed)")


def zone_absence_text(reason: ZoneAbsence) -> str:
    """Why the track cannot be coloured by zone."""
    if reason is ZoneAbsence.NO_HEART_RATE:
        return QCoreApplication.translate(
            "Map", "This activity has no heart-rate data, so the track is coloured by sport."
        )
    return QCoreApplication.translate(
        "Map",
        "No heart-rate limits are set for the day of this activity, "
        "so the track is coloured by sport.",
    )


def no_altitude_text() -> str:
    """Why there is no elevation profile."""
    return QCoreApplication.translate(
        "Map", "This activity has no altitude data, so there is no elevation profile."
    )


def no_track_text() -> str:
    """Why the selected activity has no track or profile."""
    return QCoreApplication.translate(
        "Map", "This activity has no positions (indoor or strength), so it is not on the map."
    )


def unusable_file_text(reason: object) -> str:
    """Why the original file of the selected activity could not be used."""
    why = exclusion_reason_text(reason) if isinstance(reason, ExclusionReason) else ""
    # {why}: a short reason such as "file missing from the archive".
    return QCoreApplication.translate(
        "Map", "The profile of this activity is not available: {why}."
    ).format(why=why)


def select_prompt_text() -> str:
    """Hint shown in the detail area when no activity is selected."""
    return QCoreApplication.translate(
        "Map", "Select an activity on the map or in the list to see its elevation profile."
    )


def elevation_axis_titles() -> tuple[str, str]:
    """Names of the profile's axes with units: distance and altitude."""
    return QCoreApplication.translate("Map", "Distance (km)"), QCoreApplication.translate(
        "Map", "Altitude (m)"
    )


def elevation_series_name() -> str:
    """Name of the altitude line."""
    return QCoreApplication.translate("Map", "Altitude")
