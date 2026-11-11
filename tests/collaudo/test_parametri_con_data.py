# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-CALC-05 on the real stack: parameters with a validity date, straddling activities,
recalculation.

Index, load cache and archive are real SQLite and files; the files are rebuilt from scratch in
a second profile to compare the incremental recalculation with the calculation from scratch. The
critical
dates straddle local midnight (Europe/Rome, UTC+1 in February) of the day on
which the parameters change.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import Activity, FileFormat, Sport
from ponos_coach.load.domain import compute_activity_load
from ponos_coach.load.use_cases import ProfileOverview
from tests.builders import physiology_entry
from tests.collaudo.support import RealProfile, create_profile, open_profile_from_folder
from tests.fixtures.generators import fit_writer as fw

FIRST_DAY = date(2026, 1, 1)
CHANGE_DAY = date(2026, 2, 1)  # the new parameters hold from 1 February local time
OLD_THRESHOLD, NEW_THRESHOLD = 170, 160
MINUTES = 30


def run_fit(start: datetime, serial: int, bpm: int = 150) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, bpm) for i in range(MINUTES * 60)]
    spec = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    return fw.build_activity(spec)


class CountingDecoder:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, data: bytes, file_format: FileFormat) -> Activity:
        self.calls += 1
        return decode_stored_activity(data, file_format)


# Inizi in UTC: l'ora locale (UTC+1) e a fianco.
STARTS = {
    "prima": datetime(2026, 1, 31, 22, 30, tzinfo=UTC),  # 23:30 on 31 January
    "dopo": datetime(2026, 1, 31, 23, 30, tzinfo=UTC),  # 00:30 on 1 February
    "a_cavallo": datetime(2026, 1, 31, 22, 50, tzinfo=UTC),  # 23:50, ends after midnight
    "lontana": datetime(2026, 2, 10, 7, 0, tzinfo=UTC),
}


def fill(profile: RealProfile, tmp_path: Path, folder_name: str) -> None:
    source = tmp_path / folder_name
    source.mkdir()
    for serial, start in enumerate(STARTS.values(), start=1):
        (source / f"{serial}.fit").write_bytes(run_fit(start, serial))
    profile.import_folder(source)
    assert len(profile.records()) == len(STARTS)


def set_parameters(profile: RealProfile, *, new_threshold: int = NEW_THRESHOLD) -> None:
    profile.session.physiology.put(physiology_entry(FIRST_DAY, OLD_THRESHOLD))
    profile.session.physiology.put(physiology_entry(CHANGE_DAY, new_threshold))


def expected_load(threshold: int, start: datetime) -> float:
    activity = decode_stored_activity(run_fit(start, 1), FileFormat.FIT)
    return compute_activity_load(
        Sport.RUNNING,
        activity.timer_s,
        activity.heart_rate,
        physiology_entry(FIRST_DAY, threshold).physiology,
    ).trimp_eq


def by_start(overview: ProfileOverview) -> dict[datetime, float]:
    return {a.start: a.load.trimp_eq for a in overview.activities}


def same(first: ProfileOverview, second: ProfileOverview) -> None:
    assert first.activities == second.activities
    assert first.excluded == second.excluded
    assert first.pmc.first_day == second.pmc.first_day
    for name in ("atl", "ctl", "tsb"):
        assert np.array_equal(getattr(first.pmc, name), getattr(second.pmc, name))


@pytest.fixture
def profile(tmp_path: Path) -> Iterator[RealProfile]:
    opened = create_profile(tmp_path)
    fill(opened, tmp_path, "file")
    yield opened
    opened.close()


def test_t_calc_05_activities_around_the_change_use_the_parameters_of_their_local_day(
    profile: RealProfile,
) -> None:
    set_parameters(profile)

    loads = by_start(profile.overview())

    old = {name: expected_load(OLD_THRESHOLD, STARTS[name]) for name in STARTS}
    new = {name: expected_load(NEW_THRESHOLD, STARTS[name]) for name in STARTS}
    assert old["prima"] != new["prima"]
    assert loads[STARTS["prima"]] == pytest.approx(old["prima"])  # 23:30 on 31 January
    assert loads[STARTS["a_cavallo"]] == pytest.approx(old["a_cavallo"])  # the start day counts
    assert loads[STARTS["dopo"]] == pytest.approx(new["dopo"])  # 00:30 on 1 February
    assert loads[STARTS["lontana"]] == pytest.approx(new["lontana"])


def test_t_calc_05_the_local_day_of_each_activity_is_the_start_day_not_the_end_day(
    profile: RealProfile,
) -> None:
    set_parameters(profile)

    days = {a.start: a.local_day for a in profile.overview().activities}

    assert days[STARTS["a_cavallo"]] == date(2026, 1, 31)
    assert days[STARTS["dopo"]] == date(2026, 2, 1)
    assert days[STARTS["prima"]] == date(2026, 1, 31)


def test_t_calc_05_recalculation_after_a_change_equals_the_calculation_from_scratch(
    tmp_path: Path, profile: RealProfile
) -> None:
    profile.session.physiology.put(physiology_entry(FIRST_DAY, OLD_THRESHOLD))
    profile.overview()  # all the activities in cache with the old parameters
    profile.session.physiology.put(physiology_entry(CHANGE_DAY, NEW_THRESHOLD))
    decoder = CountingDecoder()

    incremental = profile.overview(decoder)

    assert decoder.calls == 2  # only the two activities from 1 February local time are read again
    fresh = create_profile(tmp_path / "da-zero")
    try:
        fill(fresh, tmp_path, "file-da-zero")
        set_parameters(fresh)
        same(incremental, fresh.overview())
    finally:
        fresh.close()
    profile.session.load_cache.clear()
    same(incremental, profile.overview())


def test_t_calc_05_a_change_in_the_middle_of_the_history_touches_only_later_activities(
    profile: RealProfile,
) -> None:
    profile.session.physiology.put(physiology_entry(FIRST_DAY, OLD_THRESHOLD))
    before = profile.overview()
    profile.session.physiology.put(physiology_entry(date(2026, 1, 31), NEW_THRESHOLD))
    decoder = CountingDecoder()

    after = profile.overview(decoder)

    assert decoder.calls == len(STARTS)  # all from 31 January onwards
    assert by_start(after) != by_start(before)
    profile.session.load_cache.clear()
    same(after, profile.overview())


def test_t_calc_05_a_change_dated_after_every_activity_changes_nothing_and_reads_no_file(
    profile: RealProfile,
) -> None:
    set_parameters(profile)
    before = profile.overview()
    profile.session.physiology.put(physiology_entry(date(2026, 3, 1), 150))
    decoder = CountingDecoder()

    after = profile.overview(decoder)

    assert decoder.calls == 0
    same(before, after)


def test_t_calc_05_replacing_the_parameters_of_the_same_date_is_a_change_too(
    profile: RealProfile,
) -> None:
    set_parameters(profile)
    first = by_start(profile.overview())
    set_parameters(profile, new_threshold=150)  # same validity date, other value
    decoder = CountingDecoder()

    second = by_start(profile.overview(decoder))

    assert decoder.calls == 2
    assert second[STARTS["prima"]] == first[STARTS["prima"]]
    assert second[STARTS["dopo"]] != first[STARTS["dopo"]]
    assert second[STARTS["dopo"]] == pytest.approx(expected_load(150, STARTS["dopo"]))


def test_t_calc_05_the_cache_survives_closing_and_reopening_the_profile(
    tmp_path: Path, profile: RealProfile
) -> None:
    set_parameters(profile)
    before = profile.overview()
    folder, ports = profile.folder, profile.ports
    profile.close()

    reopened = open_profile_from_folder(ports, folder)
    try:
        decoder = CountingDecoder()
        after = reopened.overview(decoder)

        assert decoder.calls == 0  # no file read again: the loads come from the database
        same(before, after)
    finally:
        reopened.close()


def test_t_calc_05_the_day_before_the_first_parameters_uses_the_first_ones(
    tmp_path: Path, profile: RealProfile
) -> None:
    profile.session.physiology.put(physiology_entry(date(2026, 2, 5), NEW_THRESHOLD))

    loads = by_start(profile.overview())

    for start in STARTS.values():  # all with the same parameters, the first known
        assert loads[start] == pytest.approx(expected_load(NEW_THRESHOLD, start))
