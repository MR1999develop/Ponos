# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding for the phase 4 acceptance campaign: realistic threshold-test runs as FIT/GPX files
that go through the real stack (decoder, archive, SQLite index, use cases).

A run is a list of `Leg`s (constant HR and speed), one record per second from second 0 to the
end of the last leg (included). The distance at a record is what was covered before it, so every
expected value (segment, HR, pace) can be computed by hand from the legs.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import ActivityRecord
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.assessment.use_cases import (
    EvaluateThresholdTest,
    ListThresholdTests,
    NextRetest,
    ThresholdEvaluation,
)
from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.profiles.measurements import UpdateMeasurements
from ponos_coach.profiles.threshold_tests import ApplyThresholdTest, RemoveThresholdTest
from tests.builders import physiology_entry
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators.tracks import SEA_LAT_DEG, SEA_LON_DEG

ROME = ZoneInfo("Europe/Rome")
TODAY = date(2026, 7, 1)  # the same `TODAY` as the campaign (support.py)
START = datetime(2026, 3, 14, 7, 0, tzinfo=UTC)  # a Saturday, 08:00 in Rome
FIRST_DAY = date(2026, 1, 1)
DAY = date(2026, 3, 14)
LIMITS = (50, 190)  # the HR limits of `tests.builders.physiology`
THRESHOLD_BEFORE = 170


@dataclass(frozen=True)
class Leg:
    """`seconds` long at `bpm` (None: no HR in the records) and `speed_ms`."""

    seconds: int
    bpm: int | None
    speed_ms: float = 3.0


def standard_legs(
    *, warm_up: int = 600, hr: int = 165, speed_ms: float = 3.5, cool_down: int = 300
) -> list[Leg]:
    """Warm-up (easy), 30 minutes of test, cool-down (easy)."""
    legs = []
    if warm_up:
        legs.append(Leg(warm_up, 130, 3.0))
    legs.append(Leg(1800, hr, speed_ms))
    if cool_down:
        legs.append(Leg(cool_down, 120, 2.5))
    return legs


def run_samples(
    legs: Sequence[Leg],
    start: datetime = START,
    *,
    with_distance: bool = True,
    with_speed: bool = False,
    dropped: Collection[int] = (),
    last_second: int | None = None,
) -> list[fw.Sample]:
    """One record per second; the records at the seconds in `dropped` do not exist (a pause
    in the recording: the distance keeps growing at the next record)."""
    first = fw.datetime_to_fit(start)
    bpm: list[int | None] = []
    speed: list[float] = []
    for leg in legs:
        bpm += [leg.bpm] * leg.seconds
        speed += [leg.speed_ms] * leg.seconds
    bpm.append(bpm[-1])
    speed.append(speed[-1])
    end = len(bpm) - 1 if last_second is None else last_second
    meters = 0.0
    samples = []
    for t in range(end + 1):
        if t not in dropped:
            samples.append(
                fw.Sample(
                    first + t,
                    bpm[t],
                    distance_m=round(meters, 2) if with_distance else None,
                    speed_ms=speed[t] if with_speed else None,
                )
            )
        meters += speed[t]
    return samples


def run_fit(
    legs: Sequence[Leg],
    start: datetime = START,
    *,
    serial: int = 1_000_001,
    sport: fw.Sport = fw.Sport.RUNNING,
    sub_sport: fw.SubSport = fw.SubSport.GENERIC,
    elapsed_s: float | None = None,
    with_distance: bool = True,
    with_speed: bool = False,
    dropped: Collection[int] = (),
    last_second: int | None = None,
) -> bytes:
    """The FIT of a run. Elapsed time defaults to the total of the legs."""
    samples = run_samples(
        legs,
        start,
        with_distance=with_distance,
        with_speed=with_speed,
        dropped=dropped,
        last_second=last_second,
    )
    total = sum(leg.seconds for leg in legs) if elapsed_s is None else elapsed_s
    spec = fw.ActivitySpec(
        start,
        sport,
        sub_sport,
        samples,
        serial_number=serial,
        total_elapsed_ms=round(total * 1000),
    )
    return fw.build_activity(spec)


def gpx_run(
    legs: Sequence[Leg],
    start: datetime = START,
    *,
    with_hr: bool = True,
    sport_type: str | None = "running",
    lon_step_per_ms: float = 0.00001,
) -> bytes:
    """A GPX of a run on a straight line at the speed of each leg (one point a second)."""
    points = []
    position = 0.0
    t = 0
    for leg in legs:
        for _ in range(leg.seconds):
            points.append(_gpx_point(start, t, position, leg.bpm if with_hr else None))
            position += leg.speed_ms * lon_step_per_ms
            t += 1
    points.append(_gpx_point(start, t, position, legs[-1].bpm if with_hr else None))
    spec = gw.GpxSpec([points], sport_type=sport_type)
    return gw.build_gpx(spec)


def _gpx_point(start: datetime, t: int, lon_offset: float, hr: int | None) -> gw.GpxPoint:
    return gw.GpxPoint(
        SEA_LAT_DEG, SEA_LON_DEG + lon_offset, start + timedelta(seconds=t), None, hr
    )


@dataclass
class ThresholdProfile:
    """A real profile (SQLite, on-disk archive) with the threshold test use cases wired."""

    profile: RealProfile
    evaluate: EvaluateThresholdTest
    apply: ApplyThresholdTest
    remove: RemoveThresholdTest
    list_tests: ListThresholdTests
    next_retest: NextRetest
    measurements: UpdateMeasurements
    folder: Path
    history: ThresholdTestStore

    def import_bytes(self, data: bytes, name: str = "run.fit") -> ActivityRecord:
        """Imports one file through the real import and returns its record."""
        directory = self.folder.parent / "incoming" / name
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(data)
        before = {r.identity for r in self.profile.records()}
        summary = self.profile.import_files([path])
        assert summary.results[0].issue is None, summary.results[0]
        [record] = [r for r in self.profile.records() if r.identity not in before]
        return record

    def evaluate_bytes(self, data: bytes, name: str = "run.fit") -> ThresholdEvaluation:
        return self.evaluate(self.import_bytes(data, name).identity)

    def entries(self) -> list[PhysiologyEntry]:
        return list(self.profile.session.physiology.list_entries())

    def close(self) -> None:
        self.profile.close()


def make_threshold_profile(
    base: Path, *, today: date = TODAY, threshold: int = THRESHOLD_BEFORE
) -> ThresholdProfile:
    """A real profile whose physiology starts on `FIRST_DAY` (50-190 bpm, threshold 170)."""
    profile = create_profile(base)
    session = profile.session
    session.physiology.put(physiology_entry(FIRST_DAY, threshold))
    return wire(profile, today)


def wire(profile: RealProfile, today: date = TODAY) -> ThresholdProfile:
    session = profile.session
    evaluate = EvaluateThresholdTest(
        session.activities,
        profile.archive,
        session.physiology,
        decode_stored_activity,
        timezone=ROME,
    )
    measurements = UpdateMeasurements(
        session.activities,
        session.physiology,
        session.weights,
        lambda: today,
        session.fixed_data,
    )
    tests = session.threshold_tests
    return ThresholdProfile(
        profile,
        evaluate,
        ApplyThresholdTest(session.activities, tests, session.physiology, measurements),
        RemoveThresholdTest(session.activities, tests, session.physiology),
        ListThresholdTests(tests),
        NextRetest(tests, lambda: today),
        measurements,
        profile.folder,
        tests,
    )
