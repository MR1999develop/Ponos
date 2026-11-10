# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`GetActivityDetail` use case: track, zones valid that day, summary, load, errors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import numpy as np
import pytest

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    Activity,
    ActivityOrigin,
    ActivityRecord,
    FileFormat,
    Sport,
)
from ponos_coach.activities.ports import ActivityNotFoundError
from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.load.domain import (
    LoadSource,
    MissingPhysiologyError,
    zone_starts_bpm,
)
from ponos_coach.load.use_cases import ExcludedActivity, ExclusionReason
from tests.builders import IMPORTED_AT, fit_identity, physiology_entry
from tests.fixtures.generators import fit_writer as fw
from tests.load.test_profile_overview import (
    CHANGE_DAY,
    FIRST_PARAMS_DAY,
    ROME,
    World,
    decode_fit,
)

START = datetime(2026, 2, 10, 7, 0, tzinfo=UTC)


def add_activity(
    world: World,
    bpm: Sequence[int | None],
    *,
    serial: int = 1,
    start: datetime = START,
    with_gps: bool = False,
) -> ActivityRecord:
    """A 1 Hz activity with these bpm (None = no valid HR) in the world's archive; with
    `with_gps` it runs north-east from a made-up point, one step of 0.0001 degrees per second."""
    first = fw.datetime_to_fit(start)
    samples = [
        fw.Sample(
            first + i,
            value,
            lat_deg=45.0 + i * 0.0001 if with_gps else None,
            lon_deg=9.0 + i * 0.0001 if with_gps else None,
        )
        for i, value in enumerate(bpm)
    ]
    spec = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    data = fw.build_activity(spec)
    activity = decode_fit(data, FileFormat.FIT)
    archived = world.archive.store(data, FileFormat.FIT, activity.start)
    record = ActivityRecord.from_activity(activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT)
    world.profile.activities.add(record)
    return record


def use_case(
    world: World,
    *,
    decode: object = decode_fit,
    max_file_bytes: int = 50_000_000,
) -> GetActivityDetail:
    return GetActivityDetail(
        world.profile.activities,
        world.archive,
        world.profile.physiology,
        decode,  # type: ignore[arg-type]
        timezone=ROME,
        max_file_bytes=max_file_bytes,
    )


def detail_of(world: World, record: ActivityRecord) -> ActivityDetail:
    found = use_case(world)(record.identity)
    assert isinstance(found, ActivityDetail)
    return found


def test_detail_gives_trace_load_summary_and_zone_bounds_of_the_day() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    record = add_activity(world, [150] * 600)

    detail = detail_of(world, record)

    assert detail.identity == record.identity
    assert detail.sport is Sport.RUNNING
    assert detail.start == START
    assert detail.local_day == START.astimezone(ROME).date()
    assert (detail.elapsed_s, detail.timer_s) == (record.elapsed_s, record.timer_s)
    assert detail.summary == record.summary
    assert detail.threshold_bpm == 170
    assert (detail.limits.min_bpm, detail.limits.max_bpm) == (50, 190)
    assert (
        detail.zone_starts_bpm
        == zone_starts_bpm(170)
        == pytest.approx((132.6, 147.9, 159.8, 170.0))
    )
    assert detail.load.source is LoadSource.HEART_RATE
    assert detail.load.zone_seconds.values == (0.0, 0.0, 600.0, 0.0, 0.0)  # 150 bpm: Z3
    assert detail.trace is not None
    assert detail.trace.seconds.tolist() == list(range(600))
    assert set(detail.trace.bpm.tolist()) == {150.0}
    assert detail.trace.discarded_count == 0


def test_the_detail_load_is_the_one_of_the_overview() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    record = add_activity(world, [150] * 300 + [185] * 300)

    overview = world.overview()
    detail = detail_of(world, record)

    (entry,) = overview.activities
    assert detail.load == entry.load
    assert detail.local_day == entry.local_day


def test_out_of_limit_samples_are_marked_as_discarded_in_the_trace() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    bpm: list[int | None] = [150] * 20
    bpm[3], bpm[9] = 200, 40  # above the maximum 190, below the minimum 50
    record = add_activity(world, bpm)

    detail = detail_of(world, record)

    assert detail.trace is not None
    assert np.flatnonzero(detail.trace.discarded).tolist() == [3, 9]
    assert detail.trace.discarded_count == detail.load.discarded_samples == 2
    assert detail.trace.bpm[3] == 200.0  # the discarded sample is visible, marked


def test_limits_and_zones_are_the_ones_valid_on_the_activity_day() -> None:
    """T-CALC-05: two activities straddling a threshold and maximum HR change."""
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    world.profile.physiology.put(physiology_entry(CHANGE_DAY, 160))
    before = add_activity(world, [152] * 60, serial=1, start=datetime(2026, 1, 15, 7, tzinfo=UTC))
    after = add_activity(world, [152] * 60, serial=2, start=datetime(2026, 2, 10, 7, tzinfo=UTC))

    detail_before, detail_after = detail_of(world, before), detail_of(world, after)

    assert (detail_before.threshold_bpm, detail_after.threshold_bpm) == (170, 160)
    assert detail_after.zone_starts_bpm == pytest.approx((124.8, 139.2, 150.4, 160.0))
    assert detail_before.load.zone_seconds.values == (0.0, 0.0, 60.0, 0.0, 0.0)  # 152 = 89.4%
    assert detail_after.load.zone_seconds.values == (0.0, 0.0, 0.0, 60.0, 0.0)  # 152 = 95%


def test_an_activity_without_heart_rate_has_no_trace_and_an_estimated_load() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [None] * 600)

    detail = detail_of(world, record)

    assert detail.trace is None
    assert detail.load.source is LoadSource.ESTIMATE
    assert detail.load.zone_seconds.total == 0.0
    assert detail.zone_load is None  # an estimate does not come from the zones


def test_the_detail_decodes_the_file_once_and_never_reads_the_gps_track() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60, with_gps=True)
    calls: list[FileFormat] = []

    def counting(data: bytes, file_format: FileFormat) -> Activity:
        calls.append(file_format)
        return decode_fit(data, file_format)

    found = use_case(world, decode=counting)(record.identity)

    assert isinstance(found, ActivityDetail)
    assert calls == [FileFormat.FIT]  # one decode, and no second pass for the position track


def test_the_load_of_each_zone_adds_up_to_the_load_and_harder_zones_earn_more() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    record = add_activity(world, [140] * 300 + [175] * 300)  # 5 minutes in Z2, 5 in Z5

    detail = detail_of(world, record)

    assert detail.zone_load is not None
    z1, z2, z3, z4, z5 = detail.zone_load
    assert (z1, z3, z4) == (0.0, 0.0, 0.0)
    assert z2 + z5 == pytest.approx(detail.load.trimp_eq)
    assert z5 > 1.5 * z2  # same time, higher heart rate


def test_the_detail_reads_the_file_but_leaves_the_load_cache_alone() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60)

    detail_of(world, record)

    assert world.archive.reads == 1
    world.overview()
    assert world.archive.reads == 2  # the cache was empty: the overview had to read


def test_an_unknown_activity_is_an_error_of_the_caller() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))

    with pytest.raises(ActivityNotFoundError):
        use_case(world)(fit_identity(99))


def test_a_profile_without_parameters_cannot_show_a_detail() -> None:
    world = World()
    record = add_activity(world, [150] * 60)

    with pytest.raises(MissingPhysiologyError):
        use_case(world)(record.identity)


def test_a_missing_file_is_a_clear_outcome() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60)
    del world.archive.files[record.archived.relative_path]

    assert use_case(world)(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.FILE_MISSING
    )


def test_an_altered_file_is_a_clear_outcome() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60)
    data = bytearray(world.archive.files[record.archived.relative_path])
    data[20] ^= 0xFF  # same size, different content: the SHA-256 no longer matches
    world.archive.files[record.archived.relative_path] = bytes(data)

    assert use_case(world)(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.FILE_ALTERED
    )


def test_a_file_over_the_byte_cap_is_a_clear_outcome() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60)

    assert use_case(world, max_file_bytes=100)(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.FILE_TOO_LARGE
    )


def test_a_file_that_does_not_decode_is_a_clear_outcome() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60)

    def broken(_data: bytes, _file_format: FileFormat) -> Activity:
        raise ActivityFileError(ActivityFileReason.CORRUPT)

    assert use_case(world, decode=broken)(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.DECODE_FAILED
    )


def test_a_file_with_another_identity_counts_as_altered() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = add_activity(world, [150] * 60, serial=1)
    other = add_activity(world, [150] * 60, serial=2, start=datetime(2026, 2, 11, 7, tzinfo=UTC))

    def wrong_file(_data: bytes, file_format: FileFormat) -> Activity:
        return decode_fit(world.archive.files[other.archived.relative_path], file_format)

    assert use_case(world, decode=wrong_file)(record.identity) == ExcludedActivity(
        record.identity, ExclusionReason.FILE_ALTERED
    )


def test_the_byte_cap_must_be_positive() -> None:
    world = World()

    with pytest.raises(ValueError, match="byte"):
        use_case(world, max_file_bytes=0)
