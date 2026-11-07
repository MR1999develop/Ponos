# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Regenerates the versioned FIT fixtures in `tests/fixtures/data/`.

Usage, from the repository root: `python -m tests.fixtures.generators.make_fixtures`.
With a fixed seed and fixed dates, every run produces the same bytes.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from . import fit_writer as fw
from .tracks import (
    Track,
    add_noise,
    circle_positions,
    drift,
    hold,
    ramp,
    straight_positions,
    to_samples,
)

SEED = 20260101
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BASE = datetime(2026, 3, 2, 7, 0, 0, tzinfo=UTC)  # invented date


def _spec(
    day: int,
    sport: fw.Sport,
    sub: fw.SubSport,
    track: Track,
    serial: int,
    step_s: int = 1,
) -> fw.ActivitySpec:
    start = BASE.replace(day=BASE.day + day)
    samples = to_samples(fw.datetime_to_fit(start), track, step_s)
    return fw.ActivitySpec(start, sport, sub, samples, serial_number=serial, product=serial % 100)


def build_valid(rng: random.Random) -> dict[str, bytes]:
    """Valid files, by name."""
    run, bike, walk = fw.Sport.RUNNING, fw.Sport.CYCLING, fw.Sport.WALKING
    out: dict[str, bytes] = {}

    # T-CALC-01: exactly 60' at 160 bpm, without noise.
    out["run_constant_160.fit"] = fw.build_activity(
        _spec(0, run, fw.SubSport.GENERIC, hold(160, 3600), 5000001)
    )

    intervals = (
        ramp(100, 140, 600)
        + [v for _ in range(6) for v in (hold(175, 180) + hold(130, 120))]
        + drift(140, 600, 5)
        + ramp(140, 105, 300)
    )
    out["run_intervals.fit"] = fw.build_activity(
        _spec(1, run, fw.SubSport.ROAD, add_noise(intervals, rng), 5000002)
    )

    easy_bike = ramp(95, 125, 300) + drift(125, 2700, 8) + ramp(133, 100, 300)
    out["bike_easy.fit"] = fw.build_activity(
        _spec(2, bike, fw.SubSport.ROAD, add_noise(easy_bike, rng), 5000003)
    )

    strength = add_noise(ramp(85, 110, 600) + hold(105, 1800) + ramp(105, 90, 300), rng, 2)
    out["strength_low_hr.fit"] = fw.build_activity(
        _spec(3, fw.Sport.TRAINING, fw.SubSport.STRENGTH_TRAINING, strength, 5000004)
    )

    out["walk_no_hr.fit"] = fw.build_activity(
        _spec(4, walk, fw.SubSport.GENERIC, [None] * 2400, 5000005)
    )

    # HR gaps over 10 s: 12 s with invalid HR and 15 s without records.
    gap_track: Track = add_noise(hold(150, 1800), rng)
    gap_track[300:312] = [None] * 12
    gap_spec = _spec(5, run, fw.SubSport.GENERIC, gap_track, 5000006)
    kept = [
        s for s in gap_spec.samples if not 900 <= s.timestamp - gap_spec.samples[0].timestamp < 915
    ]
    out["run_hr_gaps.fit"] = fw.build_activity(
        fw.ActivitySpec(
            gap_spec.start,
            run,
            fw.SubSport.GENERIC,
            kept,
            serial_number=5000006,
            product=6,
            total_elapsed_ms=1800_000,
        )
    )

    # Out-of-range samples: 0 and 250 bpm scattered in a normal track.
    bad = add_noise(hold(145, 1800), rng)
    for i in (100, 101, 700, 1200):
        bad[i] = 0
    for i in (400, 401, 402, 1500):
        bad[i] = 250
    out["run_hr_out_of_range.fit"] = fw.build_activity(
        _spec(6, run, fw.SubSport.GENERIC, bad, 5000007)
    )

    # 1 s and 24 h activities (one sample every 5 s).
    out["run_one_second.fit"] = fw.build_activity(
        _spec(7, run, fw.SubSport.GENERIC, [120], 5000008)
    )
    long_track = add_noise(hold(110, 17280), rng)
    hike = _spec(8, fw.Sport.HIKING, fw.SubSport.TRAIL, long_track, 5000009, step_s=5)
    out["hike_24h.fit"] = fw.build_activity(
        fw.ActivitySpec(
            hike.start,
            hike.sport,
            hike.sub_sport,
            hike.samples,
            serial_number=hike.serial_number,
            product=hike.product,
            total_elapsed_ms=86_400_000,
        )
    )
    return out


def build_gps(rng: random.Random) -> dict[str, bytes]:
    """Files with positions and distance (invented coordinates in the open sea).

    They are generated with their own random generator: the bytes of the `build_valid` files do not
    depend on these.
    """
    out: dict[str, bytes] = {}

    # Run on a circle of 300 m radius, 3 m/s, with altitude and declared elevation gain.
    loop = circle_positions(1200, 300.0, 3.0)
    base = _spec(9, fw.Sport.RUNNING, fw.SubSport.ROAD, add_noise(hold(150, 1200), rng), 5000010)
    samples = [
        replace(
            s,
            lat_deg=lat,
            lon_deg=lon,
            distance_m=dist,
            altitude_m=100.0 + 10.0 * ((i // 100) % 2),
        )
        for i, (s, (lat, lon, dist)) in enumerate(zip(base.samples, loop, strict=True))
    ]
    out["run_gps_loop.fit"] = fw.build_activity(
        replace(
            base,
            samples=samples,
            total_distance_m=loop[-1][2],
            total_ascent_m=60,
            start_position=(loop[0][0], loop[0][1]),
        )
    )

    # Indoor bike: no position, distance from the sensor, no elevation gain.
    trainer = _spec(
        10, fw.Sport.CYCLING, fw.SubSport.INDOOR_CYCLING,
        add_noise(hold(130, 1800), rng), 5000011,
    )  # fmt: skip
    trainer_samples = [replace(s, distance_m=i * 8.0) for i, s in enumerate(trainer.samples)]
    out["bike_indoor_no_gps.fit"] = fw.build_activity(
        replace(trainer, samples=trainer_samples, total_distance_m=1799 * 8.0)
    )

    # Straight line with invalid positions at the start and in the middle; the session does not
    # declare the
    # start: the first valid record counts.
    line = straight_positions(600, 2.5)
    gaps = [*range(30), *range(300, 310)]
    straight = _spec(
        11, fw.Sport.RUNNING, fw.SubSport.GENERIC, add_noise(hold(140, 600), rng), 5000012
    )
    straight_samples = [
        s if i in gaps else replace(s, lat_deg=lat, lon_deg=lon)
        for i, (s, (lat, lon, _)) in enumerate(zip(straight.samples, line, strict=True))
    ]
    out["run_gps_invalid_positions.fit"] = fw.build_activity(
        replace(straight, samples=straight_samples)
    )
    return out


def _threshold_legs() -> list[tuple[int, int, float]]:
    """Legs `(seconds, bpm, speed in m/s)` of a threshold test: 10' warm-up, 30' test (the
    first 10' at 160 bpm, the last 20' at 168 bpm and 3.6 m/s) and 5' cool-down."""
    return [(600, 125, 3.0), (600, 160, 3.4), (1200, 168, 3.6), (300, 120, 2.5)]


def _threshold_samples(
    first: int, *, distance: bool, speed: bool = False, pause: tuple[int, int] | None = None
) -> list[fw.Sample]:
    """One record a second from 0 to the end of the legs (included), HR and, as asked, cumulative
    distance or speed. `pause=(after_s, gap_s)` leaves no record in the gap."""
    samples: list[fw.Sample] = []
    meters = 0.0
    second = 0
    for seconds, bpm, speed_ms in _threshold_legs():
        for _ in range(seconds):
            if pause is None or not pause[0] < second < pause[0] + pause[1]:
                samples.append(
                    fw.Sample(
                        first + second,
                        bpm,
                        distance_m=round(meters, 2) if distance else None,
                        speed_ms=speed_ms if speed else None,
                    )
                )
                meters += speed_ms
            second += 1
    last_bpm = _threshold_legs()[-1][1]
    samples.append(
        fw.Sample(
            first + second,
            last_bpm,
            distance_m=round(meters, 2) if distance else None,
            speed_ms=_threshold_legs()[-1][2] if speed else None,
        )
    )
    return samples


def build_threshold() -> dict[str, bytes]:
    """Runs for the threshold test, written without noise so the expected values are hand-computed.

    Test segment from second 600 to 2400; threshold 168 bpm; in the last 20' 4320 m: 278 s/km.
    """
    run = fw.Sport.RUNNING
    out: dict[str, bytes] = {}
    variants = {
        "run_threshold_test.fit": (12, 5000020, {"distance": True}),
        "run_threshold_no_distance.fit": (13, 5000021, {"distance": False}),
        "run_threshold_speed_only.fit": (14, 5000022, {"distance": False, "speed": True}),
        "run_threshold_paused.fit": (15, 5000023, {"distance": True, "pause": (1200, 90)}),
    }
    for name, (day, serial, options) in variants.items():
        start = BASE.replace(day=BASE.day + day)
        samples = _threshold_samples(fw.datetime_to_fit(start), **options)  # type: ignore[arg-type]
        spec = fw.ActivitySpec(
            start, run, fw.SubSport.GENERIC, samples, serial_number=serial, product=serial % 100
        )
        out[name] = fw.build_activity(spec)
    return out


def build_hostile(valid: bytes) -> dict[str, bytes]:
    """File ostili derivati da un file valido piccolo."""
    return {
        "header_size_too_large.fit": fw.with_declared_size(valid, len(valid) + 1000),
        "header_size_too_small.fit": fw.with_declared_size(valid, 10),
        "header_size_huge.fit": fw.huge_declared_file(),
        "header_size_max_uint32.fit": fw.with_declared_size(valid, 0xFFFFFFFF),
        "truncated_in_header.fit": fw.truncate(valid, 9),
        "truncated_mid_record.fit": fw.truncate(valid, 60),
        "truncated_no_crc.fit": fw.truncate(valid, len(valid) - 2),
        "bad_file_crc.fit": fw.with_bad_file_crc(valid),
        "bad_header_crc.fit": fw.with_bad_header_crc(valid),
        "bad_signature.fit": fw.with_bad_signature(valid),
        "undefined_local_message.fit": fw.undefined_local_message(),
        "huge_field_definition.fit": fw.huge_field_definition(),
        "empty.fit": b"",
    }


def _write(directory: Path, files: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (directory / name).write_bytes(content)


def main() -> None:
    """Writes all the fixtures."""
    rng = random.Random(SEED)
    valid = build_valid(rng)
    # Same file_id and same bytes, different name: duplicate for T-IMP.
    valid["copy_of_run_constant_160.fit"] = valid["run_constant_160.fit"]
    valid.update(build_gps(random.Random(SEED + 1)))
    valid.update(build_threshold())
    _write(DATA_DIR / "fit", valid)
    small = fw.build_activity(
        _spec(0, fw.Sport.RUNNING, fw.SubSport.GENERIC, hold(120, 5), 5000100)
    )
    _write(DATA_DIR / "fit-ostili", build_hostile(small))


if __name__ == "__main__":
    main()
