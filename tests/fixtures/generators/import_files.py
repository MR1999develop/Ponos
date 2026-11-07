# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Distinct activities, in FIT and GPX, for the import tests (generated on the fly, not versioned).

`fit_run(n)` and `gpx_run(n)` give a different run for each number (different start, serial number
and
positions). `same_run_as_fit_and_gpx` gives the same activity in the two formats: same
start, same duration, same route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import fit_writer as fw
from .gpx_writer import GpxPoint, GpxSpec, build_gpx, line_points
from .tracks import SEA_LAT_DEG, SEA_LON_DEG

FIRST_START = datetime(2026, 5, 1, 7, 0, tzinfo=UTC)
RUN_SECONDS = 1800
STEP_S = 10  # one sample every 10 s: small files, intervals within the 30 s of the GPX


def run_start(number: int) -> datetime:
    """Start of run number `number`: one per day, at the same time."""
    return FIRST_START + timedelta(days=number)


def _heart_rate(index: int) -> int:
    return 130 + index % 20


def fit_run(number: int, *, seconds: int = RUN_SECONDS) -> bytes:
    """FIT of a run of `seconds` seconds, one sample every 10 s, with position and HR."""
    start = run_start(number)
    first = fw.datetime_to_fit(start)
    samples = [
        fw.Sample(
            first + i * STEP_S,
            _heart_rate(i),
            lat_deg=SEA_LAT_DEG + i * 0.0001,
            lon_deg=SEA_LON_DEG,
            altitude_m=10.0,
        )
        for i in range(seconds // STEP_S + 1)
    ]
    spec = fw.ActivitySpec(
        start,
        fw.Sport.RUNNING,
        fw.SubSport.GENERIC,
        samples,
        serial_number=2_000_000 + number,
        total_elapsed_ms=seconds * 1000,
        total_timer_ms=seconds * 1000,
    )
    return fw.build_activity(spec)


def gpx_points(number: int, *, seconds: int = RUN_SECONDS) -> list[GpxPoint]:
    """The same samples as `fit_run`, as GPX points."""
    return line_points(
        run_start(number),
        seconds // STEP_S + 1,
        step_s=STEP_S,
        lat_step_deg=0.0001,
        ele_m=[10.0] * (seconds // STEP_S + 1),
        hr=[_heart_rate(i) for i in range(seconds // STEP_S + 1)],
    )


def gpx_run(number: int, *, seconds: int = RUN_SECONDS, sport_type: str = "running") -> bytes:
    """GPX of a run of `seconds` seconds, with altitude and HR."""
    return build_gpx(GpxSpec([gpx_points(number, seconds=seconds)], sport_type=sport_type))


def same_run_as_fit_and_gpx(number: int = 0) -> tuple[bytes, bytes]:
    """The same activity as FIT and as GPX (same start and duration)."""
    return fit_run(number), gpx_run(number)
