# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Caso d'uso `compute_fitness_overview`: storico generato, giorni locali, file rifiutati."""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.activities.fit_decoder import FitDecodeReason, FitLimits
from ponos_coach.activities.ports import (
    FitFileRef,
    FitFileTooLargeError,
    FitFileUnreadableError,
)
from ponos_coach.load.domain import LoadSource, SportWeight
from ponos_coach.load.use_cases import (
    FitnessParameters,
    RejectionReason,
    compute_fitness_overview,
)
from ponos_coach.storage.fit_folder import FitFolder
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators.history import generate_history
from tests.load.helpers import FIXTURES, PHYSIOLOGY

ROME = ZoneInfo("Europe/Rome")
UTC_ZONE = ZoneInfo("UTC")
START = date(2026, 1, 5)


class InMemorySource:
    """Fake di `FitFileSource`: file in memoria, con il tetto di byte rispettato."""

    def __init__(self, files: dict[str, bytes], broken: Sequence[str] = ()) -> None:
        self._files = files
        self._broken = set(broken)

    def list_files(self) -> list[FitFileRef]:
        return [FitFileRef(name, len(data)) for name, data in sorted(self._files.items())]

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        if ref.name in self._broken:
            raise FitFileUnreadableError(ref.name)
        data = self._files[ref.name]
        if len(data) > max_bytes:
            raise FitFileTooLargeError(ref.name)
        return data


def run_at(start: datetime, serial: int = 1, minutes: int = 30, bpm: int = 150) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, bpm) for i in range(minutes * 60)]
    spec = fw.ActivitySpec(
        start, fw.Sport.RUNNING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    return fw.build_activity(spec)


def params(
    timezone: ZoneInfo = UTC_ZONE, sport_weights: Mapping[Sport, SportWeight] | None = None
) -> FitnessParameters:
    return FitnessParameters(PHYSIOLOGY, timezone, sport_weights or {})


def test_overview_of_a_generated_history(tmp_path: Path) -> None:
    paths = generate_history(tmp_path, days=60, seed=7, start_date=START)
    today = START + timedelta(days=60)

    overview = compute_fitness_overview(FitFolder(tmp_path), params(), today)

    assert len(overview.activities) == len(paths)
    assert overview.rejected == ()
    first_day = overview.activities[0].local_day
    assert overview.pmc.first_day == first_day
    assert len(overview.pmc) == (today - first_day).days + 1
    assert overview.pmc.days[-1] == today
    assert overview.pmc.ctl[-1] > 0
    starts = [a.start for a in overview.activities]
    assert starts == sorted(starts)
    assert all(a.load.source is LoadSource.HEART_RATE for a in overview.activities)
    assert {a.sport for a in overview.activities} <= set(Sport)


def test_pmc_is_the_sum_of_the_weighted_loads_per_local_day(tmp_path: Path) -> None:
    generate_history(tmp_path, days=21, seed=3, start_date=START)
    today = START + timedelta(days=21)

    overview = compute_fitness_overview(FitFolder(tmp_path), params(), today)

    day_one_load = sum(
        a.load.load for a in overview.activities if a.local_day == overview.pmc.first_day
    )
    assert overview.pmc.atl[0] == pytest.approx(day_one_load / 7)
    assert overview.pmc.ctl[0] == pytest.approx(day_one_load / 42)


def test_overview_is_deterministic(tmp_path: Path) -> None:
    generate_history(tmp_path, days=30, seed=11, start_date=START)
    today = START + timedelta(days=30)

    one = compute_fitness_overview(FitFolder(tmp_path), params(), today)
    two = compute_fitness_overview(FitFolder(tmp_path), params(), today)

    assert one.activities == two.activities
    assert np.array_equal(one.pmc.ctl, two.pmc.ctl)
    assert np.array_equal(one.pmc.tsb, two.pmc.tsb)


def test_local_day_uses_the_timezone_of_the_parameters() -> None:
    late = datetime(2026, 3, 2, 23, 30, tzinfo=UTC)  # a Roma sono le 00:30 del giorno dopo
    source = InMemorySource({"late.fit": run_at(late)})

    in_utc = compute_fitness_overview(source, params(UTC_ZONE), date(2026, 3, 3))
    in_rome = compute_fitness_overview(source, params(ROME), date(2026, 3, 3))

    assert in_utc.activities[0].local_day == date(2026, 3, 2)
    assert in_rome.activities[0].local_day == date(2026, 3, 3)
    assert in_utc.activities[0].start == in_rome.activities[0].start == late


def test_local_day_follows_daylight_saving_time() -> None:
    winter = datetime(2026, 1, 15, 22, 30, tzinfo=UTC)  # UTC+1: 23:30, stesso giorno
    summer = datetime(2026, 7, 15, 22, 30, tzinfo=UTC)  # UTC+2: 00:30, giorno dopo
    source = InMemorySource({"w.fit": run_at(winter, 1), "s.fit": run_at(summer, 2)})

    overview = compute_fitness_overview(source, params(ROME), date(2026, 8, 1))

    assert [a.local_day for a in overview.activities] == [date(2026, 1, 15), date(2026, 7, 16)]


def test_activity_across_midnight_belongs_to_the_day_it_starts() -> None:
    start = datetime(2026, 3, 2, 23, 50, tzinfo=UTC)  # 30 minuti: finisce dopo mezzanotte
    source = InMemorySource({"x.fit": run_at(start)})

    overview = compute_fitness_overview(source, params(UTC_ZONE), date(2026, 3, 4))

    assert overview.activities[0].local_day == date(2026, 3, 2)
    assert overview.pmc.days == (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4))


def test_rejected_files_have_their_reason_and_do_not_stop_the_others() -> None:
    hostile = Path(FIXTURES / "fit-ostili")
    files = {p.name: p.read_bytes() for p in hostile.glob("*.fit")}
    files["good.fit"] = run_at(datetime(2026, 3, 2, 7, 0, tzinfo=UTC))
    source = InMemorySource(files)

    overview = compute_fitness_overview(source, params(), date(2026, 3, 2))

    assert [a.file_name for a in overview.activities] == ["good.fit"]
    reasons = {r.file_name: r.reason for r in overview.rejected}
    assert reasons["empty.fit"] is RejectionReason.EMPTY
    assert reasons["bad_file_crc.fit"] is RejectionReason.CORRUPT
    assert len(reasons) == len(files) - 1


def test_unreadable_oversized_and_duplicate_files_are_rejected_with_reason() -> None:
    day = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
    good = run_at(day)
    source = InMemorySource(
        {"a.fit": good, "b_copy.fit": good, "locked.fit": good},
        broken=["locked.fit"],
    )

    overview = compute_fitness_overview(
        source,
        FitnessParameters(PHYSIOLOGY, UTC_ZONE, fit_limits=FitLimits(max_bytes=len(good))),
        date(2026, 3, 2),
    )
    assert {r.file_name: r.reason for r in overview.rejected} == {
        "b_copy.fit": RejectionReason.DUPLICATE,
        "locked.fit": RejectionReason.UNREADABLE,
    }

    tiny = compute_fitness_overview(
        source,
        FitnessParameters(PHYSIOLOGY, UTC_ZONE, fit_limits=FitLimits(max_bytes=10)),
        date(2026, 3, 2),
    )
    assert {r.file_name: r.reason for r in tiny.rejected} == {
        "a.fit": RejectionReason.TOO_LARGE,
        "b_copy.fit": RejectionReason.TOO_LARGE,
        "locked.fit": RejectionReason.UNREADABLE,
    }
    assert tiny.activities == ()


def test_every_decode_reason_has_a_rejection_reason() -> None:
    names = {reason.name for reason in RejectionReason}

    assert {reason.name for reason in FitDecodeReason} <= names


def test_estimate_is_flagged_and_included_in_the_pmc() -> None:
    walk = (FIXTURES / "fit" / "walk_no_hr.fit").read_bytes()

    overview = compute_fitness_overview(
        InMemorySource({"w.fit": walk}), params(), date(2026, 3, 13)
    )

    (entry,) = overview.activities
    assert entry.load.source is LoadSource.ESTIMATE
    assert overview.pmc.atl[0] == pytest.approx(entry.load.load / 7)


def test_sport_weights_from_the_parameters_override_the_defaults() -> None:
    source = InMemorySource({"r.fit": run_at(datetime(2026, 3, 2, 7, 0, tzinfo=UTC))})
    today = date(2026, 3, 2)

    default = compute_fitness_overview(source, params(), today).activities[0].load.load
    double = (
        compute_fitness_overview(
            source, params(sport_weights={Sport.RUNNING: SportWeight(2.0)}), today
        )
        .activities[0]
        .load.load
    )
    zero = (
        compute_fitness_overview(
            source, params(sport_weights={Sport.RUNNING: SportWeight(0.0)}), today
        )
        .activities[0]
        .load.load
    )

    assert double == pytest.approx(2 * default)
    assert zero == 0.0


def test_empty_source_gives_an_empty_overview() -> None:
    overview = compute_fitness_overview(InMemorySource({}), params(), date(2026, 3, 2))

    assert overview.activities == ()
    assert overview.rejected == ()
    assert len(overview.pmc) == 0


def test_activities_after_today_are_listed_but_not_in_the_pmc() -> None:
    future = run_at(datetime(2026, 3, 10, 7, 0, tzinfo=UTC), serial=9)
    past = run_at(datetime(2026, 3, 1, 7, 0, tzinfo=UTC), serial=8)
    source = InMemorySource({"f.fit": future, "p.fit": past})

    overview = compute_fitness_overview(source, params(), date(2026, 3, 3))

    assert len(overview.activities) == 2
    assert overview.pmc.days[-1] == date(2026, 3, 3)
    only_future = compute_fitness_overview(
        InMemorySource({"f.fit": future}), params(), date(2026, 3, 3)
    )
    assert len(only_future.activities) == 1
    assert len(only_future.pmc) == 0


def test_parameters_copy_the_weights_mapping() -> None:
    weights = {Sport.RUNNING: SportWeight(2.0)}
    built = params(sport_weights=weights)
    weights[Sport.RUNNING] = SportWeight(0.0)

    assert built.weight_of(Sport.RUNNING).value == 2.0
    assert built.weight_of(Sport.CYCLING).value == 0.8


def test_folder_import_of_the_fixture_folder_end_to_end(tmp_path: Path) -> None:
    for path in (FIXTURES / "fit").glob("*.fit"):
        shutil.copy(path, tmp_path / path.name)

    overview = compute_fitness_overview(FitFolder(tmp_path), params(), date(2026, 3, 20))

    # run_constant_160 e la sua copia hanno lo stesso file_id: una sola entra.
    rejected = {r.file_name: r.reason for r in overview.rejected}
    # L'elenco è per nome: la copia viene prima e vince; l'originale è il duplicato.
    assert rejected == {"run_constant_160.fit": RejectionReason.DUPLICATE}
    assert len(overview.activities) == 9
