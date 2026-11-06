# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riepilogo di un'attività ricavato dal file: distanza, dislivello, partenza e traccia."""

from __future__ import annotations

import math
from dataclasses import dataclass

from ponos_coach.activities.domain.geo import MAX_POLYLINE_CHARS, GeoPoint

_POLYLINE_FIRST_CHAR = 63  # '?'
_POLYLINE_LAST_CHAR = 126  # '~'


def _check_non_negative(name: str, value: float | None) -> None:
    if value is not None and not (math.isfinite(value) and value >= 0.0):
        raise ValueError(f"{name} deve essere finito e non negativo")


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """Ciò che si ricava dal file oltre al tempo e alla FC; ogni voce può mancare.

    `distance_m` e `ascent_m` (dislivello positivo) sono in metri, finiti e non negativi.
    `start_point` è la posizione di partenza e `track_polyline` la traccia semplificata
    codificata (polyline a 1e-5 gradi, al massimo `MAX_POLYLINE_CHARS` caratteri). Sono dati
    di posizione: mai nei log. Un'attività indoor ha solo distanza e dislivello, o nulla.
    """

    distance_m: float | None = None
    ascent_m: float | None = None
    start_point: GeoPoint | None = None
    track_polyline: str | None = None

    def __post_init__(self) -> None:
        _check_non_negative("la distanza", self.distance_m)
        _check_non_negative("il dislivello", self.ascent_m)
        track = self.track_polyline
        if track is None:
            return
        if not track or len(track) > MAX_POLYLINE_CHARS:
            raise ValueError("traccia vuota o troppo lunga")
        if not all(_POLYLINE_FIRST_CHAR <= ord(c) <= _POLYLINE_LAST_CHAR for c in track):
            raise ValueError("la traccia contiene caratteri non validi per un polyline")
