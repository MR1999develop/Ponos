# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Synthetic data for the map prototype: starts and simplified tracks of N activities.

No real data: invented cities at arbitrary positions, random-walk tracks with persistent
heading. The result is `data.js` (`window.PONOS_DATA`) with three base64 arrays: starts
(lat, lon float32), per-activity offsets (int32) and track coordinates (lat, lon float32,
interleaved). Usage: `python generate_data.py [N] [seed]`.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_COUNT = 50_000
POINTS_PER_TRACK = (
    20,
    80,
)  # points of a simplified track
STEP_M = (100.0, 400.0)
METERS_PER_DEGREE = 111_195.08

# (lat, lon, weight): invented cities, half of the set concentrated in a few areas
HUBS = np.array(
    [
        (45.1, 7.7, 6),
        (45.5, 9.2, 8),
        (44.4, 8.9, 3),
        (45.4, 11.9, 3),
        (44.5, 11.3, 4),
        (43.8, 11.2, 4),
        (41.9, 12.5, 7),
        (40.8, 14.3, 4),
        (38.1, 13.4, 2),
        (39.2, 9.1, 2),
        (48.9, 2.3, 4),
        (51.5, -0.1, 4),
        (52.5, 13.4, 3),
        (40.4, -3.7, 3),
        (47.4, 8.5, 3),
        (59.3, 18.1, 2),
        (60.2, 24.9, 1),
        (50.1, 14.4, 1),
        (37.98, 23.7, 1),
        (41.0, 29.0, 1),
        (40.7, -74.0, 2),
        (37.8, -122.4, 2),
        (34.0, -118.2, 1),
        (-33.9, 151.2, 1),
        (35.7, 139.7, 1),
    ],
    dtype=np.float64,
)


def make_dataset(count: int, seed: int = 1) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Starts (N, 2), offsets (N + 1,) and track coordinates (M, 2), as float32/int32."""
    rng = np.random.default_rng(seed)
    hub = rng.choice(len(HUBS), size=count, p=HUBS[:, 2] / HUBS[:, 2].sum())
    spread = rng.gamma(2.0, 6_000.0, size=count) / METERS_PER_DEGREE  # about 12 km mean spread
    angle = rng.uniform(0.0, 2.0 * np.pi, size=count)
    start_lat = HUBS[hub, 0] + spread * np.sin(angle)
    start_lon = HUBS[hub, 1] + spread * np.cos(angle) / np.cos(np.radians(HUBS[hub, 0]))
    lengths = rng.integers(POINTS_PER_TRACK[0], POINTS_PER_TRACK[1] + 1, size=count)
    offsets = np.concatenate(([0], np.cumsum(lengths))).astype(np.int32)
    owner = np.repeat(np.arange(count), lengths)
    total = int(offsets[-1])
    turn = rng.normal(0.0, 0.25, size=total)  # persistent heading: cumulative sum per track
    heading = np.cumsum(turn)
    first = offsets[:-1]
    heading = (
        heading
        - np.repeat(heading[first], lengths)
        + np.repeat(rng.uniform(0.0, 2.0 * np.pi, size=count), lengths)
    )
    step = rng.uniform(STEP_M[0], STEP_M[1], size=total) / METERS_PER_DEGREE
    d_lat = step * np.sin(heading)
    d_lon = step * np.cos(heading) / np.cos(np.radians(start_lat[owner]))
    d_lat[first] = 0.0
    d_lon[first] = 0.0
    lat = np.cumsum(d_lat)
    lon = np.cumsum(d_lon)
    lat = lat - np.repeat(lat[first], lengths) + start_lat[owner]
    lon = lon - np.repeat(lon[first], lengths) + start_lon[owner]
    starts = np.stack([start_lat, start_lon], axis=1).astype(np.float32)
    coords = np.stack([lat, lon], axis=1).astype(np.float32)
    return starts, offsets, coords


def encode(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array).tobytes()).decode("ascii")


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_COUNT
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    starts, offsets, coords = make_dataset(count, seed)
    body = (
        f'window.PONOS_DATA={{n:{count},starts:"{encode(starts)}",'
        f'offsets:"{encode(offsets)}",coords:"{encode(coords)}"}};\n'
    )
    (HERE / "data.js").write_text(body, encoding="ascii")
    print(f"{count} activities, {coords.shape[0]} points, data.js {len(body) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
