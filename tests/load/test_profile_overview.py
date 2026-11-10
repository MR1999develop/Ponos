# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ComputeProfileOverview` use case: cache, parameter change, excluded, cancellation."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from ponos_coach.activities.decoding import ActivityFileError, ActivityFileReason
from ponos_coach.activities.domain import (
    Activity,
    ActivityOrigin,
    ActivityRecord,
    ArchivedFile,
    FileFormat,
)
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.load.domain import (
    MissingPhysiologyError,
    compute_activity_load,
)
from ponos_coach.load.use_cases import (
    ComputeProfileOverview,
    Decoder,
    ExclusionReason,
    OverviewCancelledError,
    ProfileOverview,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from ponos_coach.profiles.ports import OpenProfile
from tests.builders import IMPORTED_AT, absolute_path, physiology, physiology_entry
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fixtures.generators import fit_writer as fw

ROME = ZoneInfo("Europe/Rome")
TODAY = date(2026, 3, 1)
FIRST_PARAMS_DAY = date(2026, 1, 1)
CHANGE_DAY = date(2026, 2, 1)


class CountingArchive(InMemoryActivityArchive):
    """In-memory archive that counts the reads."""

    def __init__(self) -> None:
        super().__init__()
        self.reads = 0

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        self.reads += 1
        return super().read(file, max_bytes)


def decode_fit(data: bytes, file_format: FileFormat) -> Activity:
    assert file_format is FileFormat.FIT
    return decode_activity(data, FitLimits())


def run_bytes(start: datetime, serial: int, bpm: int = 150, minutes: int = 30) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, bpm) for i in range(minutes * 60)]
    spec = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    return fw.build_activity(spec)


class World:
    """An in-memory profile with its archive and the use case to start."""

    def __init__(self) -> None:
        self.archive = CountingArchive()
        profile_id = ProfileId.parse("00000000-0000-4000-8000-000000000001")
        self.profile: OpenProfile = InMemoryProfileStorage().create(
            absolute_path("p"), profile_id, ProfileName.parse("Prova"), "Europe/Rome"
        )
        self.records: list[ActivityRecord] = []

    def add_run(self, start: datetime, serial: int, bpm: int = 150) -> ActivityRecord:
        data = run_bytes(start, serial, bpm)
        activity = decode_fit(data, FileFormat.FIT)
        archived = self.archive.store(data, FileFormat.FIT, activity.start)
        record = ActivityRecord.from_activity(
            activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT
        )
        self.profile.activities.add(record)
        self.records.append(record)
        return record

    def overview(
        self,
        *,
        cancel_after: int | None = None,
        progress: list[tuple[int, int]] | None = None,
        max_file_bytes: int = 50_000_000,
        decode: Decoder = decode_fit,
    ) -> ProfileOverview:
        checks = 0

        def is_cancelled() -> bool:
            nonlocal checks
            checks += 1
            return cancel_after is not None and checks > cancel_after

        return ComputeProfileOverview(
            self.profile.activities,
            self.archive,
            self.profile.physiology,
            self.profile.load_cache,
            decode,
            timezone=ROME,
            today=lambda: TODAY,
            max_file_bytes=max_file_bytes,
            is_cancelled=is_cancelled,
            report=(lambda done, total: progress.append((done, total)))
            if progress is not None
            else (lambda _done, _total: None),
        ).execute()


def two_runs(world: World) -> tuple[ActivityRecord, ActivityRecord]:
    """One run before and one after the parameter change of 1 February."""
    before = world.add_run(datetime(2026, 1, 15, 7, 0, tzinfo=UTC), serial=1)
    after = world.add_run(datetime(2026, 2, 10, 7, 0, tzinfo=UTC), serial=2)
    return before, after


def test_empty_profile_gives_an_empty_overview_without_needing_parameters() -> None:
    result = World().overview()

    assert result.activities == ()
    assert result.excluded == ()
    assert len(result.pmc) == 0


def test_activities_without_any_parameter_are_an_explicit_error() -> None:
    world = World()
    two_runs(world)

    with pytest.raises(MissingPhysiologyError):
        world.overview()


def test_load_summary_and_local_day_of_each_activity() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    record = world.add_run(datetime(2026, 1, 15, 23, 30, tzinfo=UTC), serial=1)

    result = world.overview()

    (entry,) = result.activities
    assert entry.local_day == date(2026, 1, 16)  # 00:30 in Rome
    assert entry.summary == record.summary
    assert entry.timer_s == record.timer_s
    assert result.pmc.first_day == date(2026, 1, 16)
    assert result.pmc.days[-1] == TODAY


def test_second_run_uses_the_cache_and_reads_no_file() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)

    first = world.overview()
    assert world.archive.reads == 2
    second = world.overview()

    assert world.archive.reads == 2
    assert second.activities == first.activities
    assert np.array_equal(second.pmc.ctl, first.pmc.ctl)


def test_changed_parameters_invalidate_the_cache() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    two_runs(world)
    world.overview()

    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 160))
    world.overview()

    assert world.archive.reads == 4


def test_parameter_change_on_a_date_applies_to_later_activities_only() -> None:
    """T-CALC-05: before the change the old parameters apply, after it the new ones."""
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    world.profile.physiology.put(physiology_entry(CHANGE_DAY, 160))
    before, after = two_runs(world)

    result = world.overview()

    for record, threshold in ((before, 170), (after, 160)):
        activity = decode_fit(world.archive.read(record.archived, 50_000_000), FileFormat.FIT)
        expected = compute_activity_load(
            activity.sport, activity.timer_s, activity.heart_rate, physiology(threshold)
        )
        (found,) = [a for a in result.activities if a.identity == record.identity]
        assert found.load == expected
    assert result.activities[0].load != result.activities[1].load


def test_full_recalculation_equals_calculation_from_scratch_after_a_change() -> None:
    """T-CALC-05: after the change, partial cache and full recomputation give the same result."""
    incremental = World()
    incremental.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    two_runs(incremental)
    incremental.overview()
    incremental.profile.physiology.put(physiology_entry(CHANGE_DAY, 160))
    after_change = incremental.overview()  # the first activity stays in cache

    from_scratch = World()
    from_scratch.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY, 170))
    from_scratch.profile.physiology.put(physiology_entry(CHANGE_DAY, 160))
    two_runs(from_scratch)
    expected = from_scratch.overview()

    incremental.profile.load_cache.clear()
    full = incremental.overview()

    assert after_change.activities == expected.activities
    assert full.activities == expected.activities
    assert np.array_equal(full.pmc.ctl, expected.pmc.ctl)
    assert np.array_equal(after_change.pmc.atl, expected.pmc.atl)


def test_days_before_the_first_parameter_use_the_first_one() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(date(2026, 2, 1), 160))
    world.add_run(datetime(2026, 1, 15, 7, 0, tzinfo=UTC), serial=1)
    reference = World()
    reference.profile.physiology.put(physiology_entry(date(2026, 1, 1), 160))
    reference.add_run(datetime(2026, 1, 15, 7, 0, tzinfo=UTC), serial=1)

    assert world.overview().activities == reference.overview().activities


def test_missing_file_is_excluded_and_the_others_are_computed() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    before, after = two_runs(world)
    del world.archive.files[before.archived.relative_path]

    result = world.overview()

    assert [(e.identity, e.reason) for e in result.excluded] == [
        (before.identity, ExclusionReason.FILE_MISSING)
    ]
    assert [a.identity for a in result.activities] == [after.identity]


def test_altered_file_is_excluded() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    before, _ = two_runs(world)
    world.archive.files[before.archived.relative_path] += b"x"

    result = world.overview()

    assert [e.reason for e in result.excluded] == [ExclusionReason.FILE_ALTERED]
    assert len(result.activities) == 1


def test_file_over_the_byte_limit_is_excluded() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)

    result = world.overview(max_file_bytes=100)

    assert [e.reason for e in result.excluded] == [ExclusionReason.FILE_TOO_LARGE] * 2
    assert result.activities == ()
    assert len(result.pmc) == 0


def test_undecodable_file_is_excluded() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)

    def failing(_data: bytes, _file_format: FileFormat) -> Activity:
        raise ActivityFileError(ActivityFileReason.CORRUPT)

    result = world.overview(decode=failing)

    assert [e.reason for e in result.excluded] == [ExclusionReason.DECODE_FAILED] * 2


def test_excluded_activity_is_retried_once_its_file_is_back() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    before, _ = two_runs(world)
    content = world.archive.files.pop(before.archived.relative_path)
    assert len(world.overview().excluded) == 1

    world.archive.files[before.archived.relative_path] = content

    assert world.overview().excluded == ()


def test_cancellation_raises_and_keeps_the_loads_already_computed() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)

    with pytest.raises(OverviewCancelledError):
        world.overview(cancel_after=1)
    assert world.archive.reads == 1

    result = world.overview()

    assert world.archive.reads == 2  # only the second activity, the first is in cache
    assert len(result.activities) == 2


def test_progress_is_reported_from_zero_to_the_total() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)
    progress: list[tuple[int, int]] = []

    world.overview(progress=progress)

    assert progress == [(0, 2), (1, 2), (2, 2)]


def test_pmc_covers_the_days_from_the_first_activity_to_today() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_runs(world)

    pmc = world.overview().pmc

    assert pmc.first_day == date(2026, 1, 15)
    assert len(pmc) == (TODAY - date(2026, 1, 15)).days + 1
    assert pmc.days[-1] == TODAY
