# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""What passes between Python and the map page: validation of the page's messages and payloads.

The page is ours and isolated, but it is still the web: whatever it sends is parsed here into a
valid value or rejected (`None`), and nothing it sends can name a file, a path or an activity
other than by its position in the list that Python itself sent. Payloads to the page are plain
JSON objects (`json.dumps`, no NaN), never HTML, and only through a closed set of functions.
Positions are personal data: nothing here logs them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from ponos_coach.activities.domain import (
    GeoBox,
    GeoPoint,
    identity_key,
)
from ponos_coach.activities.map_queries import MapTrack, SearchResult
from ponos_coach.load.activity_profile import ActivityProfileData
from ponos_coach.ui.map_style import sport_index

PAGE_FUNCTIONS: Final = frozenset(
    {
        "init",
        "setStarts",
        "setTracks",
        "setSelected",
        "focusSelected",
        "setProfile",
        "setProfileView",
        "setCursor",
        "setTheme",
        "fitAll",
        "setView",
        "zoomBy",
        "clearProfile",
        "refreshTiles",
    }
)
MAX_MAP_ZOOM: Final = 19  # highest zoom the page allows (the topographic tiles end there)
_MAX_LONGITUDE_ABS: Final = 3600.0  # ten turns of the globe: a map never shows more
_COORDINATE_DECIMALS: Final = 5  # about one metre
_JSON_SEPARATORS: Final = (",", ":")


@dataclass(frozen=True, slots=True)
class MapView:
    """The area the page shows and its zoom (0 to 19)."""

    box: GeoBox
    zoom: int


def _finite_number(value: object) -> float | None:
    """The value as a float if it is a finite int or float (not a bool), else `None`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_view(
    south: object, west: object, north: object, east: object, zoom: object
) -> MapView | None:
    """The view the page reports, or `None` if it is not a plausible one.

    A view that extends beyond the globe is fine (a map shows copies of the world); non-finite
    numbers, absurd longitudes and zooms outside the map's range are not.
    """
    edges = [_finite_number(v) for v in (south, west, north, east)]
    if isinstance(zoom, bool) or not isinstance(zoom, int) or None in edges:
        return None
    s, w, n, e = (v for v in edges if v is not None)
    if not 0 <= zoom <= MAX_MAP_ZOOM or max(abs(w), abs(e)) > _MAX_LONGITUDE_ABS:
        return None
    try:
        return MapView(GeoBox.from_view(s, w, n, e), zoom)
    except ValueError:
        return None


def parse_activity_index(value: object, count: int) -> int | None:
    """The position of an activity in the list that Python sent, or `None` if it is outside."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value < count else None


def _rounded(value: float) -> float:
    return round(value, _COORDINATE_DECIMALS)


def starts_message(search: SearchResult, generation: int = 0) -> dict[str, object]:
    """Start points of the filtered activities: `lat, lon, index, sport` repeated, flat.

    `generation` numbers the list the indexes refer to; the page sends it back with every click.
    """
    data: list[float | int] = []
    for position, lat, lon in zip(
        search.start_indices.tolist(),
        search.start_lat_deg.tolist(),
        search.start_lon_deg.tolist(),
        strict=True,
    ):
        data += (
            _rounded(lat),
            _rounded(lon),
            position,
            sport_index(search.activities[position].sport),
        )
    return {"gen": generation, "data": data}


def _flat(points: Sequence[GeoPoint]) -> list[float]:
    flat: list[float] = []
    for point in points:
        flat += (_rounded(point.lat_deg), _rounded(point.lon_deg))
    return flat


def index_by_identity(search: SearchResult) -> dict[str, int]:
    """Position of every filtered activity, by identity key."""
    return {identity_key(r.identity): i for i, r in enumerate(search.activities)}


def tracks_message(
    tracks: Sequence[MapTrack], index_of: Mapping[str, int], generation: int = 0
) -> dict[str, object]:
    """Simplified tracks in view; a track whose activity is not in the list is left out."""
    items: list[dict[str, object]] = []
    for track in tracks:
        position = index_of.get(identity_key(track.identity))
        if position is not None:
            items.append(
                {
                    "idx": position,
                    "sport": sport_index(track.sport),
                    "segments": [_flat(segment) for segment in track.segments],
                }
            )
    return {"gen": generation, "tracks": items}


def profile_message(profile: ActivityProfileData, generation: int) -> dict[str, object]:
    """Points of the selected activity's profile for the page (the heavy part).

    The position in the list and the colour mode change without the points changing: they go in
    `profile_view_message`. `generation` numbers these points; the page sends it back with the
    index of a hovered point.
    """
    return {
        "gen": generation,
        "lat": [_rounded(v) for v in profile.lat_deg.tolist()],
        "lon": [_rounded(v) for v in profile.lon_deg.tolist()],
        "segment": profile.segment.tolist(),
        "zone": None if profile.zone is None else profile.zone.tolist(),
    }


def profile_view_message(position: int | None, mode: str) -> dict[str, object]:
    """Which activity of the list the profile belongs to and how to colour it."""
    return {"idx": position, "mode": mode}


def page_call(function: str, payload: object) -> str:
    """JavaScript that calls a page function with a JSON argument.

    Raises:
        ValueError: the function is not one the page offers.
    """
    if function not in PAGE_FUNCTIONS:
        raise ValueError("not a page function")
    body = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=_JSON_SEPARATORS)
    return f"ponosMap.{function}({body});"
