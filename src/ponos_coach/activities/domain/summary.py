# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Summary of an activity derived from the file: distance, ascent, start and track."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ponos_coach.activities.domain.geo import MAX_POLYLINE_CHARS, GeoPoint

_POLYLINE_FIRST_CHAR = 63  # '?'
_POLYLINE_LAST_CHAR = 126  # '~'


def _check_non_negative(name: str, value: float | None) -> None:
    if value is not None and not (math.isfinite(value) and value >= 0.0):
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """What is derived from the file besides time and HR; each item may be missing.

    `distance_m` and `ascent_m` (positive elevation gain) are in meters, finite and non-negative.
    `start_point` is the starting position and `track_polyline` the simplified track
    encoded (polyline at 1e-5 degrees, at most `MAX_POLYLINE_CHARS` characters). They are
    position data: never in logs. An indoor activity has only distance and ascent, or nothing.
    """

    distance_m: float | None = None
    ascent_m: float | None = None
    start_point: GeoPoint | None = None
    track_polyline: str | None = None

    def __post_init__(self) -> None:
        _check_non_negative("distance", self.distance_m)
        _check_non_negative("ascent", self.ascent_m)
        track = self.track_polyline
        if track is None:
            return
        if not track or len(track) > MAX_POLYLINE_CHARS:
            raise ValueError("empty or too long track")
        if not all(_POLYLINE_FIRST_CHAR <= ord(c) <= _POLYLINE_LAST_CHAR for c in track):
            raise ValueError("the track contains characters not valid for a polyline")
