# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GetActivityProfile` use case: altitude over distance and the HR zone of each stretch.

T-MAP-08 (profile and map coincide, no altitude = no profile with the reason) and T-MAP-09 (the
zone of a stretch is the one the load computation gives to the same sample), with real FIT files.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from ponos_coach.activities.decoding import decode_stored_track
from ponos_coach.activities.domain import (
    ActivityOrigin,
    ActivityRecord,
    FileFormat,
    Sport,
)
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.activities.ports import ActivityNotFoundError
from ponos_coach.load.activity_profile import (
    MAX_PROFILE_POINTS,
    NO_ZONE,
    ActivityProfileData,
    GetActivityProfile,
    NoTrack,
    ZoneAbsence,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
    compute_activity_load,
    zone_starts_bpm,
)
from ponos_coach.load.use_cases import ExcludedActivity, ExclusionReason
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import IMPORTED_AT, absolute_path, fit_identity, physiology_entry
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fixtures.generators import fit_writer as fw

ROME = ZoneInfo("Europe/Rome")
START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
LIMITS = HeartRateLimits(50, 190)


def decode_fit(data: bytes, file_format: FileFormat):  # type: ignore[no-untyped-def]  # noqa: ANN201
    assert file_format is FileFormat.FIT
    return decode_activity(data, FitLimits())


def sample(
    first: int, i: int, *, bpm: int | None, with_altitude: bool = True, with_distance: bool = True
) -> fw.Sample:
    """One record per second along a straight run north at 3 m/s."""
    return fw.Sample(
        first + i,
        bpm,
        45.0 + i * 3.0 / 111_320.0,
        9.0,
        100.0 + i * 0.1 if with_altitude else None,
        i * 3.0 if with_distance else None,
    )


def run_file(samples: list[fw.Sample], serial: int = 1) -> bytes:
    spec = fw.ActivitySpec(
        START, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    return fw.build_activity(spec)


class World:
    """A profile with parameters, an archive and the use case."""

    def __init__(self, *, entries: list[PhysiologyEntry] | None = None) -> None:
        self.archive = InMemoryActivityArchive()
        self.profile: OpenProfile = InMemoryProfileStorage().create(
            absolute_path("p"),
            ProfileId.parse("00000000-0000-4000-8000-000000000001"),
            ProfileName.parse("Atleta di prova"),
            "Europe/Rome",
        )
        for entry in entries if entries is not None else [physiology_entry(date(2026, 1, 1), 170)]:
            self.profile.physiology.put(entry)
        self.use_case = GetActivityProfile(
            self.profile.activities,
            self.archive,
            self.profile.physiology,
            decode_fit,
            decode_stored_track,
            timezone=ROME,
        )

    def add(self, data: bytes) -> ActivityRecord:
        activity = decode_fit(data, FileFormat.FIT)
        archived = self.archive.store(data, FileFormat.FIT, activity.start)
        record = ActivityRecord.from_activity(
            activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT
        )
        self.profile.activities.add(record)
        return record

    def profile_of(self, data: bytes) -> ActivityProfileData:
        result = self.use_case(self.add(data).identity)
        assert isinstance(result, ActivityProfileData)
        return result


def first_second() -> int:
    return fw.datetime_to_fit(START)


def test_t_map_08_the_profile_columns_follow_the_track_in_distance_and_altitude() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(120)])

    profile = World().profile_of(data)

    assert len(profile) == 120
    assert profile.distance_m[0] == 0.0
    assert profile.distance_m[-1] == pytest.approx(119 * 3.0, abs=0.5)
    assert np.all(np.diff(profile.distance_m) >= 0)
    assert profile.altitude_m is not None
    assert profile.altitude_m[0] == pytest.approx(100.0, abs=0.5)
    assert profile.altitude_m[-1] == pytest.approx(100.0 + 119 * 0.1, abs=0.5)
    assert profile.lat_deg[0] == pytest.approx(45.0, abs=1e-4)
    assert profile.lat_deg[-1] > profile.lat_deg[0]
    assert set(profile.segment.tolist()) == {0}
    assert not profile.distance_m.flags.writeable


def test_t_map_08_each_profile_point_is_the_map_point_with_the_same_index() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(60)])
    world = World()
    record = world.add(data)
    track = decode_stored_track(data, FileFormat.FIT)
    assert track is not None

    profile = world.use_case(record.identity)

    assert isinstance(profile, ActivityProfileData)
    assert np.array_equal(profile.lat_deg, np.concatenate([s.lat_deg for s in track.segments]))
    assert np.array_equal(profile.lon_deg, np.concatenate([s.lon_deg for s in track.segments]))


def test_t_map_08_without_altitude_there_is_no_profile_and_the_track_stays() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150, with_altitude=False) for i in range(30)])

    profile = World().profile_of(data)

    assert profile.altitude_m is None
    assert len(profile) == 30


def test_without_a_distance_series_the_distance_is_measured_along_the_track() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150, with_distance=False) for i in range(100)])

    profile = World().profile_of(data)

    assert profile.distance_m[-1] == pytest.approx(99 * 3.0, rel=0.02)


def test_a_gap_in_the_positions_starts_a_new_segment() -> None:
    first = first_second()
    samples = [sample(first, i, bpm=150) for i in range(20)]
    samples += [sample(first, i, bpm=150) for i in range(80, 100)]  # 60 s without records

    profile = World().profile_of(run_file(samples))

    assert sorted(set(profile.segment.tolist())) == [0, 1]
    assert (np.diff(profile.segment) >= 0).all()


def test_a_long_activity_is_reduced_to_at_most_the_profile_points_keeping_the_ends() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(5000)])

    profile = World().profile_of(data)

    assert len(profile) <= MAX_PROFILE_POINTS
    assert profile.distance_m[0] == 0.0
    assert profile.distance_m[-1] == pytest.approx(4999 * 3.0, abs=0.5)


def test_an_activity_without_positions_is_reported_as_having_no_track() -> None:
    first = first_second()
    samples = [fw.Sample(first + i, 140) for i in range(30)]
    world = World()
    record = world.add(run_file(samples))

    assert world.use_case(record.identity) == NoTrack(record.identity)


def test_a_missing_original_is_reported_with_its_reason() -> None:
    first = first_second()
    world = World()
    record = world.add(run_file([sample(first, i, bpm=150) for i in range(30)]))
    world.archive.files.clear()

    result = world.use_case(record.identity)

    assert result == ExcludedActivity(record.identity, ExclusionReason.FILE_MISSING)


def test_an_unknown_identity_is_an_error() -> None:
    with pytest.raises(ActivityNotFoundError):
        World().use_case(fit_identity(99))


# --- T-MAP-09: the zone of a stretch is the load computation's zone of the same sample --------


def test_t_map_09_zones_come_from_the_threshold_of_the_day_and_match_the_legend() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(30)])

    profile = World(entries=[physiology_entry(date(2026, 1, 1), 170)]).profile_of(data)

    assert profile.threshold_bpm == 170
    assert profile.zone_starts_bpm == zone_starts_bpm(170)
    assert profile.zone is not None
    assert set(profile.zone.tolist()) == {2}  # 150 / 170 = 88%: from 87% it is Z3


def test_t_map_09_the_parameters_valid_on_the_local_day_are_the_ones_used() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(30)])
    entries = [
        physiology_entry(date(2026, 1, 1), 170),
        physiology_entry(date(2026, 3, 2), 150),  # from the activity's own local day
    ]

    profile = World(entries=entries).profile_of(data)

    assert profile.threshold_bpm == 150
    assert profile.local_day == date(2026, 3, 2)


def zone_seconds_of_profile(profile: ActivityProfileData) -> list[float]:
    assert profile.zone is not None
    return [float(np.count_nonzero(profile.zone == z)) for z in range(5)]


def test_t_map_09_the_seconds_in_each_zone_equal_the_load_computation() -> None:
    first = first_second()
    bpms = [60, 100, 130, 140, 150, 160, 168, 175, 185, 189] * 12  # every zone
    bpms[7] = 45  # below the minimum HR: the load discards it
    bpms[8] = 200  # above the maximum HR: discarded too
    samples = [sample(first, i, bpm=b) for i, b in enumerate(bpms)]
    data = run_file(samples)
    world = World()
    activity = decode_fit(data, FileFormat.FIT)
    physiology = Physiology(LIMITS, ThresholdHeartRate(170), Sex.MALE)
    load = compute_activity_load(Sport.RUNNING, activity.timer_s, activity.heart_rate, physiology)

    profile = world.profile_of(data)

    assert profile.zone is not None
    assert zone_seconds_of_profile(profile) == pytest.approx(list(load.zone_seconds.values))
    discarded = np.flatnonzero(profile.zone == NO_ZONE)
    assert discarded.tolist() == [7, 8]


def test_t_map_09_a_hole_in_the_heart_rate_is_not_painted_with_the_last_zone() -> None:
    first = first_second()
    samples = [sample(first, i, bpm=150) for i in range(20)]
    samples += [sample(first, i, bpm=None) for i in range(20, 50)]  # position but no HR
    samples += [sample(first, i, bpm=150) for i in range(50, 70)]

    profile = World().profile_of(run_file(samples))

    assert profile.zone is not None
    seconds = np.arange(70)
    in_hole = (seconds >= 20) & (seconds < 50)
    # the sample at second 19 is valid for 31 s, longer than the load counts: the hole is open
    assert (profile.zone[in_hole] == NO_ZONE).all()
    assert (profile.zone[seconds < 19] != NO_ZONE).all()
    assert (profile.zone[seconds >= 50] != NO_ZONE).all()


def test_without_heart_rate_there_are_no_zones_and_the_reason_is_given() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=None) for i in range(30)])

    profile = World().profile_of(data)

    assert profile.zone is None
    assert profile.zone_absence is ZoneAbsence.NO_HEART_RATE
    assert profile.threshold_bpm is None


def test_without_physiological_parameters_there_are_no_zones_and_the_reason_is_given() -> None:
    first = first_second()
    data = run_file([sample(first, i, bpm=150) for i in range(30)])

    profile = World(entries=[]).profile_of(data)

    assert profile.zone is None
    assert profile.zone_absence is ZoneAbsence.NO_PARAMETERS
    assert profile.altitude_m is not None  # the rest of the profile is still there
