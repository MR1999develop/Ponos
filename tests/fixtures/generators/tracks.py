# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tracciati di frequenza cardiaca sintetici, un valore per campione.

Un tracciato è una lista di `int | None` (None = FC assente). Il rumore usa
solo un generatore con seme esplicito, così i byte sono ripetibili.
"""

from __future__ import annotations

import random

from .fit_writer import Sample

Track = list[int | None]


def hold(bpm: int, count: int) -> Track:
    """FC costante per `count` campioni."""
    return [bpm] * count


def ramp(start_bpm: int, end_bpm: int, count: int) -> Track:
    """Rampa lineare da `start_bpm` a `end_bpm` in `count` campioni."""
    if count <= 1:
        return [end_bpm] * count
    return [round(start_bpm + (end_bpm - start_bpm) * i / (count - 1)) for i in range(count)]


def drift(bpm: int, count: int, extra_bpm: int) -> Track:
    """Deriva cardiaca: FC che sale lentamente di `extra_bpm` nel tratto."""
    return ramp(bpm, bpm + extra_bpm, count)


def add_noise(track: Track, rng: random.Random, amplitude: int = 1) -> Track:
    """Rumore intero uniforme in [-amplitude, +amplitude]; i None restano None."""
    return [None if v is None else v + rng.randint(-amplitude, amplitude) for v in track]


def to_samples(first_timestamp: int, track: Track, step_s: int = 1) -> list[Sample]:
    """Trasforma il tracciato in campioni a passo costante."""
    return [Sample(first_timestamp + i * step_s, v) for i, v in enumerate(track)]
