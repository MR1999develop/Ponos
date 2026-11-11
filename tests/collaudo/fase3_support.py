# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding for the phase 3 acceptance campaign: mixed activities, "from scratch" profiles and
comparisons.

The activities are synthetic FIT files (run, trail, bike, strength, hike without HR) with HR
segments that touch all five zones. A "from scratch" profile receives the same files and the same
final parameters as a profile that changed them step by step: its overview, computed
with an empty cache, is the oracle of the incremental recomputation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.load.use_cases import ProfileOverview
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import fit_writer as fw

STEP_S = 2  # one sample every 2 s: small files, intervals well under 10 s
TODAY = date(2026, 7, 1)  # the same `TODAY` as the campaign (support.py)

type Segments = Sequence[tuple[int | None, int]]  # (bpm or None, seconds)

# Threshold 170: Z1 below 144.5, Z2 up to 153, Z3 up to 161.5, Z4 up to 170, Z5 from 170 up.
EVERY_ZONE: Segments = ((120, 300), (150, 300), (158, 300), (165, 300), (172, 300), (185, 300))
EASY: Segments = ((110, 1800),)
NO_HEART_RATE: Segments = ((None, 1800),)


@dataclass(frozen=True, slots=True)
class MixedActivity:
    """A synthetic activity of the campaign."""

    name: str
    start: datetime
    sport: fw.Sport
    sub_sport: fw.SubSport
    segments: Segments


MIXED: tuple[MixedActivity, ...] = (
    MixedActivity(
        "corsa", datetime(2026, 1, 12, 7, 0, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.GENERIC, EVERY_ZONE,
    ),
    MixedActivity(
        "bici", datetime(2026, 1, 14, 16, 0, tzinfo=UTC), fw.Sport.CYCLING,
        fw.SubSport.ROAD, EVERY_ZONE,
    ),
    MixedActivity(  # 23:30 on 31 January, Rome time: local day before the change
        "mezzanotte", datetime(2026, 1, 31, 22, 30, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.GENERIC, EVERY_ZONE,
    ),
    MixedActivity(
        "forza", datetime(2026, 2, 1, 17, 0, tzinfo=UTC), fw.Sport.TRAINING,
        fw.SubSport.STRENGTH_TRAINING, EASY,
    ),
    MixedActivity(
        "trail", datetime(2026, 3, 5, 6, 0, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.TRAIL, EVERY_ZONE,
    ),
    MixedActivity(
        "escursione", datetime(2026, 3, 20, 8, 0, tzinfo=UTC), fw.Sport.HIKING,
        fw.SubSport.GENERIC, NO_HEART_RATE,
    ),
)  # fmt: skip

CHANGE_DAYS = (
    date(2025, 12, 1),  # before every activity
    date(2026, 1, 12),  # the day of an activity
    date(2026, 1, 31),  # the day of the straddling activity
    date(2026, 2, 1),
    date(2026, 2, 15),  # in mezzo
    date(2026, 3, 20),
    date(2026, 6, 30),  # after every activity
    TODAY,  # the date of the initial entry: it replaces
)


def segmented_fit(serial: int, spec: MixedActivity) -> bytes:
    """FIT of an activity with HR in segments (`None`: sample without HR)."""
    first = fw.datetime_to_fit(spec.start)
    samples: list[fw.Sample] = []
    elapsed = 0
    for bpm, seconds in spec.segments:
        for _ in range(seconds // STEP_S):
            samples.append(fw.Sample(first + elapsed, bpm))
            elapsed += STEP_S
    activity = fw.ActivitySpec(
        spec.start, spec.sport, spec.sub_sport, samples, serial_number=serial,
        total_elapsed_ms=elapsed * 1000, total_timer_ms=elapsed * 1000,
    )  # fmt: skip
    return fw.build_activity(activity)


def write_mixed(folder: Path, specs: Sequence[MixedActivity] = MIXED) -> Path:
    """Writes the activities in `folder` (a new folder) and returns it."""
    folder.mkdir(parents=True)
    for serial, spec in enumerate(specs, start=3_000_000):
        (folder / f"{spec.name}.fit").write_bytes(segmented_fit(serial, spec))
    return folder


def profile_with_mixed(base: Path, name: str = "primo") -> RealProfile:
    """Real profile (`base/name`) with the mixed activities already imported."""
    profile = create_profile(base / name, folder_name="profilo")
    profile.import_folder(write_mixed(base / name / "file"))
    assert len(profile.records()) == len(MIXED)
    return profile


def scratch_overview(
    base: Path,
    name: str,
    entries: Sequence[PhysiologyEntry],
) -> tuple[ProfileOverview, RealProfile]:
    """Overview of a new profile with the same files and the given final parameters.

    The cache is empty: every load is computed from the file. The profile stays open (the
    caller closes it) for further comparisons, for example on the overview of a second read.
    """
    scratch = profile_with_mixed(base, name)
    for entry in entries:
        scratch.session.physiology.put(entry)
    return scratch.overview(), scratch


def assert_same_overview(first: ProfileOverview, second: ProfileOverview, today: date) -> None:
    """Same activities, same exclusions, same PMC series and same aggregations, bit for bit."""
    assert first.activities == second.activities
    assert first.excluded == second.excluded
    assert first.pmc.first_day == second.pmc.first_day
    for series in ("atl", "ctl", "tsb"):
        assert np.array_equal(getattr(first.pmc, series), getattr(second.pmc, series))
    load_a, load_b = first.weekly_load(today), second.weekly_load(today)
    assert load_a.weeks == load_b.weeks
    assert load_a.sports == load_b.sports
    assert np.array_equal(load_a.load, load_b.load)
    zones_a, zones_b = first.weekly_zones(today), second.weekly_zones(today)
    assert zones_a.weeks == zones_b.weeks
    assert np.array_equal(zones_a.seconds, zones_b.seconds)
