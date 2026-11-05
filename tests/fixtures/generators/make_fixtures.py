# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Rigenera le fixture FIT versionate in `tests/fixtures/data/`.

Uso, dalla radice del repository: `python -m tests.fixtures.generators.make_fixtures`.
Con seme fisso e date fisse, ogni esecuzione produce gli stessi byte.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

from . import fit_writer as fw
from .tracks import Track, add_noise, drift, hold, ramp, to_samples

SEED = 20260101
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BASE = datetime(2026, 3, 2, 7, 0, 0, tzinfo=UTC)  # data inventata


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
    """File validi, per nome."""
    run, bike, walk = fw.Sport.RUNNING, fw.Sport.CYCLING, fw.Sport.WALKING
    out: dict[str, bytes] = {}

    # T-CALC-01: 60' esatti a 160 bpm, senza rumore.
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

    # Buchi di FC oltre 10 s: 12 s con FC non valida e 15 s senza record.
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

    # Campioni fuori limiti: 0 e 250 bpm sparsi in un tracciato normale.
    bad = add_noise(hold(145, 1800), rng)
    for i in (100, 101, 700, 1200):
        bad[i] = 0
    for i in (400, 401, 402, 1500):
        bad[i] = 250
    out["run_hr_out_of_range.fit"] = fw.build_activity(
        _spec(6, run, fw.SubSport.GENERIC, bad, 5000007)
    )

    # Attività di 1 s e di 24 h (un campione ogni 5 s).
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
    """Scrive tutte le fixture."""
    rng = random.Random(SEED)
    valid = build_valid(rng)
    # Stesso file_id e stessi byte, nome diverso: duplicato per T-IMP.
    valid["copy_of_run_constant_160.fit"] = valid["run_constant_160.fit"]
    _write(DATA_DIR / "fit", valid)
    small = fw.build_activity(
        _spec(0, fw.Sport.RUNNING, fw.SubSport.GENERIC, hold(120, 5), 5000100)
    )
    _write(DATA_DIR / "fit-ostili", build_hostile(small))


if __name__ == "__main__":
    main()
