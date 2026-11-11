# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synthetic history of mixed activities, written in a folder of choice.

Serves the tests and the app skeleton; no history file is versioned.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from . import fit_writer as fw
from .tracks import Track, add_noise, drift, ramp, to_samples

_STEP_S = 10  # one sample every 10 s: small files

# (sport, sub_sport, average HR, minimum and maximum duration in minutes, weight in the choice)
_KINDS: list[tuple[fw.Sport, fw.SubSport, int, int, int, int]] = [
    (fw.Sport.RUNNING, fw.SubSport.ROAD, 152, 30, 75, 5),
    (fw.Sport.CYCLING, fw.SubSport.ROAD, 138, 45, 120, 3),
    (fw.Sport.TRAINING, fw.SubSport.STRENGTH_TRAINING, 105, 30, 60, 2),
    (fw.Sport.WALKING, fw.SubSport.GENERIC, 100, 30, 70, 1),
]


def _track(avg_bpm: int, minutes: int, rng: random.Random) -> Track:
    """Warm-up, central stretch with drift, cool-down."""
    total = minutes * 60 // _STEP_S
    warm = max(1, total // 8)
    cool = max(1, total // 10)
    core = total - warm - cool
    track = (
        ramp(avg_bpm - 40, avg_bpm - 5, warm)
        + drift(avg_bpm - 5, core, 8)
        + ramp(avg_bpm + 3, avg_bpm - 30, cool)
    )
    return add_noise(track, rng, 2)


def _activity_days(days: int, rng: random.Random) -> list[int]:
    """Indices of the days with activities: 4-6 for each week (even partial)."""
    chosen: list[int] = []
    for week_start in range(0, days, 7):
        week = list(range(week_start, min(week_start + 7, days)))
        count = min(len(week), rng.randint(4, 6))
        chosen.extend(sorted(rng.sample(week, count)))
    return chosen


def generate_history(out_dir: Path, days: int, seed: int, start_date: date) -> list[Path]:
    """Writes in `out_dir` the history of `days` days from `start_date`.

    Returns the paths in date order.

    Same seed and same arguments, same bytes. The files are `YYYY-MM-DD_NN_sport.fit`.
    """
    if days < 0:
        raise ValueError("days must be >= 0")
    rng = random.Random(seed)
    out_dir.mkdir(parents=True, exist_ok=True)
    weights = [kind[5] for kind in _KINDS]
    paths: list[Path] = []
    for index, day in enumerate(_activity_days(days, rng)):
        sport, sub, avg_bpm, low, high, _ = rng.choices(_KINDS, weights=weights)[0]
        minutes = rng.randint(low, high)
        hour = rng.choice((6, 7, 12, 17, 18))
        moment = datetime.combine(start_date + timedelta(days=day), datetime.min.time(), tzinfo=UTC)
        moment += timedelta(hours=hour, minutes=rng.randint(0, 59))
        track = _track(avg_bpm + rng.randint(-6, 6), minutes, rng)
        samples = to_samples(fw.datetime_to_fit(moment), track, _STEP_S)
        spec = fw.ActivitySpec(
            moment,
            sport,
            sub,
            samples,
            serial_number=7000000 + seed % 1000,
            product=index % 50 + 1,
        )
        path = out_dir / f"{moment:%Y-%m-%d}_{index:03d}_{sport.name.lower()}.fit"
        path.write_bytes(fw.build_activity(spec))
        paths.append(path)
    return paths
