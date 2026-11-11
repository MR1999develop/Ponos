# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 9 acceptance campaign, the activity detail: speed and pace over time, the active
calories (one rule for the detail, the Map and the day's nutrition) and the elevation profile with
its zones, on files that are hard on them: no distance, no heart rate, long pauses, standing still,
an absurd device total.

Real profile (SQLite, on-disk archive, real FIT decoder); the FIT files are written by
`fit_writer`. The speed is compared with a plain per-sample loop written here. The athlete is
invented ("Atleta di prova").
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.decoding import decode_stored_activity, decode_stored_track
from ponos_coach.activities.domain import ActivityIdentity, DistanceSeries, Sport
from ponos_coach.activities.domain.speed_series import (
    MIN_MOVING_SPEED_M_S,
    PAUSE_GAP_S,
    SPEED_WINDOW_S,
    smoothed_speed,
)
from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.load.activity_profile import (
    ActivityProfileData,
    GetActivityProfile,
    NoTrack,
    ZoneAbsence,
)
from ponos_coach.nutrition.domain import EnergySource
from ponos_coach.profiles.day_nutrition import (
    GetActivityEnergy,
    GetRecordEnergy,
    NutritionSources,
)
from tests.builders import fit_identity
from tests.collaudo.fase5_support import ROME, TODAY, World, make_world
from tests.fixtures.generators import fit_writer as fw

# --- The speed series: a plain loop as the oracle ---------------------------------------------


def naive_speed(seconds: list[float], meters: list[float]) -> list[float]:
    """Speed at each record by the definition: the distance covered in the 30 s window centred on
    it, cut at the first and last record of its stretch (a gap over 10 s starts a new one),
    divided by the length of the cut window; below the moving threshold, NaN."""
    stretch = [0]
    for before, after in pairwise(seconds):
        stretch.append(stretch[-1] + (1 if after - before > PAUSE_GAP_S else 0))

    def at(t: float) -> float:
        for i in range(len(seconds) - 1):
            if seconds[i] <= t <= seconds[i + 1]:
                width = seconds[i + 1] - seconds[i]
                return meters[i] + (meters[i + 1] - meters[i]) * (t - seconds[i]) / width
        return meters[-1] if t >= seconds[-1] else meters[0]

    result = []
    for i, t in enumerate(seconds):
        members = [seconds[j] for j in range(len(seconds)) if stretch[j] == stretch[i]]
        low = max(t - SPEED_WINDOW_S / 2, members[0])
        high = min(t + SPEED_WINDOW_S / 2, members[-1])
        value = (at(high) - at(low)) / (high - low) if high > low else math.nan
        result.append(value if value >= MIN_MOVING_SPEED_M_S else math.nan)
    return result


@st.composite
def distance_series(draw: st.DrawFn) -> DistanceSeries:
    """Stretches of running at a speed or standing still, with pauses of any length between."""
    seconds: list[float] = [0.0]
    meters: list[float] = [float(draw(st.integers(0, 50)))]
    for _ in range(draw(st.integers(1, 6))):
        speed = draw(st.sampled_from([0.0, 0.3, 0.5, 1.4, 3.0, 5.5, 12.0]))
        for _ in range(draw(st.integers(1, 40))):
            step = draw(st.sampled_from([1.0, 1.0, 1.0, 2.0, 5.0, 10.0]))
            seconds.append(seconds[-1] + step)
            meters.append(meters[-1] + speed * step * draw(st.sampled_from([1.0, 1.0, 0.8, 1.2])))
        if draw(st.booleans()):
            seconds[-1] += draw(st.sampled_from([10.5, 11.0, 60.0, 900.0]))  # a pause
    return DistanceSeries(np.array(seconds), np.array(meters))


@pytest.mark.collaudo
@settings(max_examples=200, deadline=None)
@given(distance_series())
def test_t_calc_the_speed_equals_a_per_sample_loop_and_a_pause_is_never_a_number(
    distance: DistanceSeries,
) -> None:
    expected = naive_speed(list(distance.seconds), list(distance.meters))

    series = smoothed_speed(distance)

    if all(math.isnan(v) for v in expected):
        assert series is None  # nothing moves: no chart
        return
    assert series is not None
    known = dict(zip(series.seconds, series.meters_per_second, strict=True))
    for t, want in zip(distance.seconds, expected, strict=True):
        got = known[t]
        assert (math.isnan(got) and math.isnan(want)) or got == pytest.approx(
            want, rel=1e-9, abs=1e-9
        )
    for before, after in zip(distance.seconds, distance.seconds[1:], strict=False):
        if after - before > PAUSE_GAP_S:  # a hole point in the middle: no line across the pause
            middle = [v for t, v in known.items() if before < t < after]
            assert middle and all(math.isnan(v) for v in middle)
    finite = series.meters_per_second[np.isfinite(series.meters_per_second)]
    assert (finite >= MIN_MOVING_SPEED_M_S).all()
    highest = max(np.diff(distance.meters) / np.diff(distance.seconds), default=0.0)
    assert (finite <= highest + 1e-9).all()  # an average never beats the fastest step


def test_the_speed_of_a_file_that_never_moves_or_has_one_record_is_nothing_not_an_error() -> None:
    still = DistanceSeries(np.arange(600, dtype=float), np.full(600, 25.0))
    one = DistanceSeries(np.array([0.0]), np.array([0.0]))
    crawling = DistanceSeries(np.arange(600, dtype=float), np.arange(600, dtype=float) * 0.4)

    assert smoothed_speed(still) is None
    assert smoothed_speed(one) is None
    assert smoothed_speed(crawling) is None  # 1.4 km/h is fidgeting, not a pace
    with pytest.raises(ValueError, match="limit"):
        smoothed_speed(still, limit=3)


def test_a_ten_minute_pause_in_a_steady_run_is_one_hole_and_the_pace_on_both_sides_is_exact() -> (
    None
):
    first = np.arange(0.0, 600.0)
    second = np.arange(1200.0, 1800.0)
    seconds = np.concatenate([first, second])
    meters = np.concatenate([first * 3.0, 1800.0 + (second - 1200.0) * 3.0])

    series = smoothed_speed(DistanceSeries(seconds, meters))

    assert series is not None
    speed = series.meters_per_second
    holes = np.flatnonzero(np.isnan(speed))
    assert holes.size == 1  # the pause, not a peak and not a bridge
    assert series.seconds[holes[0]] == pytest.approx(900.0, abs=1.0)
    assert np.nanmax(speed) == pytest.approx(3.0) and np.nanmin(speed) == pytest.approx(3.0)


@pytest.mark.collaudo
def test_t_perf_the_speed_of_a_twelve_hour_recording_is_thinned_and_quick() -> None:
    seconds = np.arange(0.0, 12 * 3600.0)
    meters = np.cumsum(np.full(seconds.size, 2.8))

    series = smoothed_speed(DistanceSeries(seconds, meters))

    assert series is not None
    assert len(series) <= 4000
    assert np.nanmax(series.meters_per_second) == pytest.approx(2.8)


# --- The detail of a file, end to end -----------------------------------------------------------


START = datetime(2026, 2, 20, 7, 0, tzinfo=UTC)
SERIAL = iter(range(7_733_001, 7_733_999))


def run_samples(
    seconds: int,
    *,
    bpm: int | None = 150,
    speed: float = 3.0,
    distance: bool = True,
    pause: tuple[int, int] | None = None,
    position: bool = False,
    start: datetime = START,
) -> list[fw.Sample]:
    first = fw.datetime_to_fit(start)
    samples = []
    for t in range(seconds + 1):
        if pause is not None and pause[0] < t < pause[1]:
            continue
        moved = speed * (t - (pause[1] - pause[0] if pause and t >= pause[1] else 0))
        samples.append(
            fw.Sample(
                first + t,
                bpm,
                lat_deg=46.123456 + moved * 9e-6 if position else None,
                lon_deg=11.654321 if position else None,
                altitude_m=300.0 + t * 0.05 if position else None,
                distance_m=round(moved, 2) if distance else None,
            )
        )
    return samples


def fit_file(
    samples: list[fw.Sample],
    *,
    kcal: int | None = None,
    timer_s: int = 1800,
    start: datetime = START,
) -> bytes:
    spec = fw.ActivitySpec(
        start,
        fw.Sport.RUNNING,
        fw.SubSport.GENERIC,
        samples,
        serial_number=next(SERIAL),
        total_elapsed_ms=timer_s * 1000,
        total_calories_kcal=kcal,
    )
    return fw.build_activity(spec)


def import_fit(world: World, data: bytes, name: str) -> ActivityIdentity:
    folder = world.base / "incoming" / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.fit"
    path.write_bytes(data)
    before = {r.identity for r in world.profile.records()}
    summary = world.profile.import_files([path])
    assert summary.results[0].issue is None, summary.results[0]
    [record] = [r for r in world.profile.records() if r.identity not in before]
    return record.identity


@pytest.fixture
def world(tmp_path: Path) -> Iterator[World]:
    made = make_world(tmp_path, today=date(2026, 3, 2))
    yield made
    made.close()


def sources_of(world: World) -> NutritionSources:
    s = world.profile.session
    return NutritionSources(
        s.activities,
        world.profile.archive,
        s.load_cache,
        s.physiology,
        s.weights,
        s.fixed_data,
        s.activity_levels,
        s.meal_times,
        s.goal_periods,
        s.lean_masses,
    )


def detail_of(world: World, identity: ActivityIdentity) -> ActivityDetail:
    s = world.profile.session
    result = GetActivityDetail(
        s.activities, world.profile.archive, s.physiology, decode_stored_activity, timezone=ROME
    )(identity)
    assert isinstance(result, ActivityDetail), result
    return result


def test_t_ui_09_a_file_without_distance_has_a_detail_with_no_speed_chart_and_a_load(
    world: World,
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800, distance=False)), "no-distance")

    detail = detail_of(world, identity)

    assert detail.speed is None
    assert detail.trace is not None and detail.load.trimp_eq > 0


def test_t_ui_09_a_file_without_heart_rate_has_a_speed_chart_a_met_energy_and_no_trace(
    world: World,
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800, bpm=None)), "no-hr")

    detail = detail_of(world, identity)
    energy = GetActivityEnergy(sources_of(world))(detail)

    assert detail.trace is None and detail.speed is not None
    assert detail.speed.meters_per_second[
        np.isfinite(detail.speed.meters_per_second)
    ].mean() == pytest.approx(3.0, rel=0.02)
    assert energy is not None and energy.source is EnergySource.MET
    assert 0 <= energy.net_kcal <= energy.total_kcal


def test_t_ui_09_a_file_where_the_athlete_never_moves_has_no_speed_chart_and_still_a_detail(
    world: World,
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800, speed=0.0)), "standing")

    detail = detail_of(world, identity)

    assert detail.speed is None
    assert detail.summary.distance_m in (0.0, None)


def test_t_ui_09_a_long_pause_makes_a_hole_in_the_speed_and_the_detail_still_reads(
    world: World,
) -> None:
    identity = import_fit(
        world, fit_file(run_samples(2400, pause=(600, 1500)), timer_s=2400), "pause"
    )

    detail = detail_of(world, identity)

    assert detail.speed is not None
    speed = detail.speed
    in_pause = (speed.seconds > 600) & (speed.seconds < 1500)
    assert in_pause.any() and np.isnan(speed.meters_per_second[in_pause]).all()
    outside = np.isfinite(speed.meters_per_second)
    assert outside.any() and speed.meters_per_second[outside].max() == pytest.approx(3.0, rel=0.05)


# --- Active calories: one rule for the detail, the Map and the day's nutrition ------------------


@pytest.mark.parametrize(
    ("kcal", "expected"),
    [
        (300, EnergySource.DECLARED),
        (749, EnergySource.DECLARED),
        (751, EnergySource.HEART_RATE),
        (None, EnergySource.HEART_RATE),
    ],
)
def test_t_nut_09_the_device_total_is_trusted_up_to_1500_kcal_an_hour_and_never_capped(
    world: World, kcal: int | None, expected: EnergySource
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800), kcal=kcal), f"kcal-{kcal}")

    energy = GetActivityEnergy(sources_of(world))(detail_of(world, identity))

    assert energy is not None
    assert energy.source is expected  # 30 min: the ceiling is 750 kcal, as 1500 an hour
    if expected is EnergySource.DECLARED:
        assert energy.total_kcal == kcal
    else:
        assert energy.total_kcal != kcal and energy.total_kcal < 1500
    assert 0 <= energy.net_kcal <= energy.total_kcal


def test_t_nut_09_the_detail_the_map_and_the_day_give_the_same_energy_for_the_same_activity(
    world: World,
) -> None:
    def at(hour: int) -> datetime:
        return START + timedelta(hours=hour)  # the same local day, one run after the other

    cases = {
        "declared": fit_file(run_samples(1800, start=at(0)), kcal=420, start=at(0)),
        "hr": fit_file(run_samples(1800, bpm=165, start=at(2)), start=at(2)),
        "met": fit_file(run_samples(1800, bpm=None, start=at(4)), start=at(4)),
        "absurd": fit_file(run_samples(1800, start=at(6)), kcal=3000, start=at(6)),
    }
    identities = [(name, import_fit(world, data, name)) for name, data in cases.items()]
    sources = sources_of(world)
    from_map = GetRecordEnergy(sources, decode_stored_activity, ROME)

    for name, identity in identities:
        detail = detail_of(world, identity)
        by_detail = GetActivityEnergy(sources)(detail)
        assert by_detail is not None, name
        assert from_map(identity) == by_detail, name
        report = world.report(detail.local_day)
        listed = [a.energy for a in report.activities if a.identity == identity]
        assert listed == [by_detail], name


# --- The elevation profile with its zones -------------------------------------------------------


def profile_of(world: World, identity: ActivityIdentity) -> ActivityProfileData | NoTrack:
    s = world.profile.session
    result = GetActivityProfile(
        s.activities,
        world.profile.archive,
        s.physiology,
        decode_stored_activity,
        decode_stored_track,
        timezone=ROME,
    )(identity)
    assert isinstance(result, ActivityProfileData | NoTrack), result
    return result


def test_the_profile_of_a_file_with_positions_follows_the_distance_and_the_zones(
    world: World,
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800, position=True)), "positions")

    profile = profile_of(world, identity)

    assert isinstance(profile, ActivityProfileData)
    assert profile.altitude_m is not None and profile.zone is not None
    assert (np.diff(profile.distance_m) >= 0).all()
    assert profile.distance_m[-1] == pytest.approx(5400.0, rel=0.02)
    assert (profile.zone == profile.zone[0]).all()  # a constant 150 bpm: one zone


def test_the_profile_of_a_file_without_positions_is_no_track_not_an_error(world: World) -> None:
    identity = import_fit(world, fit_file(run_samples(1800)), "indoor")

    assert isinstance(profile_of(world, identity), NoTrack)


def test_the_profile_without_heart_rate_has_the_chart_and_says_why_it_has_no_zones(
    world: World,
) -> None:
    identity = import_fit(world, fit_file(run_samples(1800, bpm=None, position=True)), "no-hr-gps")

    profile = profile_of(world, identity)

    assert isinstance(profile, ActivityProfileData)
    assert profile.zone is None and profile.zone_absence is not None
    assert profile.altitude_m is not None


@pytest.mark.collaudo
@settings(max_examples=100, deadline=None)
@given(st.one_of(st.floats(-1e9, 1e9), st.sampled_from([math.nan, math.inf, -math.inf])))
def test_the_pointer_time_of_any_value_maps_to_the_nearest_point_or_nothing_never_an_error(
    seconds: float,
) -> None:
    profile = ActivityProfileData(
        identity=fit_identity(1),
        sport=Sport.RUNNING,
        local_day=TODAY,
        seconds=np.arange(5, dtype=float),
        segment=np.zeros(5, dtype=np.intp),
        distance_m=np.arange(5, dtype=float),
        lat_deg=np.zeros(5),
        lon_deg=np.zeros(5),
        altitude_m=None,
        zone=None,
        zone_absence=ZoneAbsence.NO_HEART_RATE,
        threshold_bpm=None,
        zone_starts_bpm=None,
    )

    index = profile.index_at(seconds)

    if math.isfinite(seconds):
        assert index is not None and 0 <= index < 5
        nearest = min(abs(profile.seconds - seconds))
        assert abs(profile.seconds[index] - seconds) <= nearest + 1e-6
    else:
        assert index is None
