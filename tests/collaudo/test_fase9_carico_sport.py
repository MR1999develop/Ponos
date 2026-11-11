# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-CALC for the thresholds by sport group: TRIMPeq, zones (Z2 from 72%), LT1 and LT2 of the
running group and of the other sports, a threshold that changes in the middle of the history, an
activity without heart rate.

Oracles written here with no call to the functions under test: a per-sample loop for the load, the
zone and domain boundaries by whole-number arithmetic, and the overview of a profile recomputed from
scratch for the history cases.
"""

from __future__ import annotations

import math
from datetime import timedelta
from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ponos_coach.activities.domain import HeartRateSeries, Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    LoadSource,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
    compute_activity_load,
    load_fingerprint,
    threshold_for_sport,
)
from ponos_coach.load.domain.activity_load import domain_seconds
from ponos_coach.load.domain.heart_rate import SportGroup, lt1_for_sport, sport_group
from ponos_coach.load.domain.zones import seconds_per_zone
from tests.collaudo.fase5_support import TODAY, make_world

SPORTS = tuple(Sport)
MIN_BPM, MAX_BPM = 50, 190


def constant(bpm: float, seconds: int) -> HeartRateSeries:
    return HeartRateSeries(np.arange(seconds, dtype=float), np.full(seconds, float(bpm)))


def physiology_of(
    threshold: int,
    *,
    other: int | None = None,
    run_lt1: int | None = None,
    other_lt1: int | None = None,
    sex: Sex = Sex.MALE,
) -> Physiology:
    def lt(value: int | None) -> ThresholdHeartRate | None:
        return None if value is None else ThresholdHeartRate(value)

    return Physiology(
        HeartRateLimits(MIN_BPM, MAX_BPM),
        ThresholdHeartRate(threshold),
        sex,
        other_threshold=lt(other),
        run_lt1=lt(run_lt1),
        other_lt1=lt(other_lt1),
    )


def naive_load(
    sport: Sport, samples: list[tuple[float, float]], physiology: Physiology, moving_s: float
) -> float:
    """TRIMPeq with a plain loop over the samples, written from the definition in the docstring of
    `banister`: each sample counts for the seconds until the next one (the last one for the median
    gap), samples out of the limits or after a gap over 10 s do not count."""
    gaps = [b[0] - a[0] for a, b in pairwise(samples)]
    gaps.append(float(np.median(gaps)) if gaps else 1.0)
    low, high = physiology.limits.min_bpm, physiology.limits.max_bpm
    reserve = high - low
    k = physiology.sex.banister_k

    def hrr_t(s: Sport) -> float:
        return (threshold_for_sport(physiology, s) - low) / reserve

    def weight(x: float) -> float:
        return x * 0.64 * math.exp(k * x)

    total = 0.0
    for (_, bpm), seconds in zip(samples, gaps, strict=True):
        if low <= bpm <= high and seconds <= 10.0:
            hrr = (bpm - low) / reserve
            equivalent = min(1.0, hrr * hrr_t(Sport.RUNNING) / hrr_t(sport))
            total += seconds / 60.0 * weight(equivalent)
    del moving_s
    return 100.0 * total / (60.0 * weight(hrr_t(Sport.RUNNING)))


@st.composite
def physiologies(draw: st.DrawFn) -> Physiology:
    threshold = draw(st.integers(MIN_BPM + 2, MAX_BPM))
    other = draw(st.none() | st.integers(MIN_BPM + 2, MAX_BPM))
    other_effective = threshold if other is None else other
    run_lt1 = draw(st.none() | st.integers(MIN_BPM + 1, threshold - 1))
    other_lt1 = draw(st.none() | st.integers(MIN_BPM + 1, other_effective - 1))
    return physiology_of(
        threshold,
        other=other,
        run_lt1=run_lt1,
        other_lt1=other_lt1,
        sex=draw(st.sampled_from(Sex)),
    )


# --- The scale: one hour at the threshold of the sport is 100, for every sport -------------------


@pytest.mark.collaudo
@settings(deadline=None)
@given(physiologies(), st.sampled_from(SPORTS))
def test_t_calc_one_hour_at_the_threshold_of_the_sport_is_100_whatever_the_group(
    physiology: Physiology, sport: Sport
) -> None:
    lt2 = threshold_for_sport(physiology, sport)

    load = compute_activity_load(sport, 3600.0, constant(lt2, 3600), physiology)

    assert load.source is LoadSource.HEART_RATE
    assert load.trimp_eq == pytest.approx(100.0, rel=1e-9)


@pytest.mark.collaudo
@settings(deadline=None)
@given(
    physiologies(),
    st.sampled_from(SPORTS),
    st.lists(st.integers(30, 230), min_size=2, max_size=80),
    st.lists(st.sampled_from([1, 1, 1, 2, 5, 11, 40]), min_size=80, max_size=80),
)
def test_t_calc_the_vectorized_load_equals_a_per_sample_loop_for_any_sport_and_thresholds(
    physiology: Physiology, sport: Sport, bpms: list[int], steps: list[int]
) -> None:
    seconds = np.cumsum([0, *steps[: len(bpms) - 1]]).astype(float)
    series = HeartRateSeries(seconds, np.array(bpms, dtype=float))
    moving = float(seconds[-1] + 1)
    expected = naive_load(sport, list(zip(seconds, bpms, strict=True)), physiology, moving)

    load = compute_activity_load(sport, moving, series, physiology)

    if load.source is LoadSource.HEART_RATE:
        assert load.trimp_eq == pytest.approx(expected, rel=1e-9, abs=1e-12)
        assert load.trimp_eq >= 0
    else:  # at least 30% of the time is not usable: the estimate of the sport replaces it
        assert load.used_samples == 0
        assert load.trimp_eq > 0


@pytest.mark.collaudo
@settings(deadline=None)
@given(physiologies(), st.sampled_from(SPORTS), st.integers(60, 5400))
def test_t_calc_a_harder_effort_never_has_a_lower_load_for_any_sport(
    physiology: Physiology, sport: Sport, seconds: int
) -> None:
    previous = -1.0
    for bpm in range(MIN_BPM, MAX_BPM + 1, 7):
        load = compute_activity_load(sport, float(seconds), constant(bpm, seconds), physiology)
        assert load.trimp_eq >= previous
        previous = load.trimp_eq


def test_the_other_sports_threshold_changes_only_the_other_sports() -> None:
    base = physiology_of(170)
    split = physiology_of(170, other=150)
    series = constant(150, 1800)

    for sport in SPORTS:
        a = compute_activity_load(sport, 1800.0, series, base)
        b = compute_activity_load(sport, 1800.0, series, split)
        if sport_group(sport) is SportGroup.RUNNING:
            assert a == b
        else:
            assert b.trimp_eq > a.trimp_eq  # 150 bpm is the threshold of the bike, not a jog
    assert load_fingerprint(base) != load_fingerprint(split)
    assert load_fingerprint(split) == load_fingerprint(physiology_of(170, other=150))
    assert load_fingerprint(base) == load_fingerprint(physiology_of(170, other=170))
    assert load_fingerprint(base) == load_fingerprint(physiology_of(170, run_lt1=120))


# --- Zones: Z1 below 72%, Z2 from 72% -----------------------------------------------------------


@pytest.mark.parametrize("threshold", range(60, 191))
def test_t_calc_the_z1_z2_boundary_is_the_whole_bpm_at_or_above_72_percent_for_every_threshold(
    threshold: int,
) -> None:
    first_z2 = -(-72 * threshold // 100)  # ceiling, whole-number arithmetic
    bpm = np.array([first_z2 - 1, first_z2, first_z2 + 1], dtype=float)

    zones = seconds_per_zone(bpm, np.ones(3), threshold)

    assert zones.values[0] == 1.0  # one bpm under 72% is Z1
    assert zones.values[1] == 2.0  # the first whole bpm at or above 72%, and the next, are Z2
    assert first_z2 * 100 >= 72 * threshold > (first_z2 - 1) * 100


@pytest.mark.collaudo
@settings(deadline=None)
@given(
    st.integers(60, 190),
    st.lists(st.integers(30, 230), min_size=1, max_size=60),
    st.lists(st.integers(1, 9), min_size=60, max_size=60),
)
def test_t_calc_the_seconds_per_zone_equal_a_naive_count_and_add_up_to_the_counted_time(
    threshold: int, bpms: list[int], steps: list[int]
) -> None:
    seconds = np.array(steps[: len(bpms)], dtype=float)
    zones = seconds_per_zone(np.array(bpms, dtype=float), seconds, threshold)
    naive = [0.0] * 5
    for bpm, dt in zip(bpms, seconds, strict=True):
        index = sum(bpm * 100 >= p * threshold for p in (72, 85, 95, 103))
        naive[index] += dt

    assert zones.values == pytest.approx(tuple(naive))
    assert zones.total == pytest.approx(float(seconds.sum()))


# --- LT1 and LT2 by group -------------------------------------------------------------------------


@pytest.mark.collaudo
@settings(deadline=None)
@given(physiologies(), st.sampled_from(SPORTS))
def test_t_calc_lt1_is_below_or_at_lt2_and_inside_the_limits_for_every_sport_and_parameters(
    physiology: Physiology, sport: Sport
) -> None:
    lt1, lt2 = lt1_for_sport(physiology, sport), threshold_for_sport(physiology, sport)

    assert physiology.limits.min_bpm < lt1 <= lt2 <= physiology.limits.max_bpm


@pytest.mark.collaudo
@settings(deadline=None)
@given(
    physiologies(), st.sampled_from(SPORTS), st.lists(st.integers(30, 230), min_size=2, max_size=40)
)
def test_t_calc_the_domains_use_the_groups_own_lt1_and_lt2_and_split_the_counted_time(
    physiology: Physiology, sport: Sport, bpms: list[int]
) -> None:
    series = HeartRateSeries(np.arange(len(bpms), dtype=float), np.array(bpms, dtype=float))
    domains = domain_seconds(sport, series, physiology)
    lt1, lt2 = lt1_for_sport(physiology, sport), threshold_for_sport(physiology, sport)
    in_limits = [b for b in bpms if MIN_BPM <= b <= MAX_BPM]
    # every sample lasts one second, the last one the median (also one second)
    assert domains is not None
    assert domains.below_lt1 == sum(1 for b in in_limits if b < lt1)
    assert domains.between == sum(1 for b in in_limits if lt1 <= b < lt2)
    assert domains.above_lt2 == sum(1 for b in in_limits if b >= lt2)


def test_a_group_with_nothing_entered_falls_back_to_the_running_lt2_and_its_own_default_lt1() -> (
    None
):
    physiology = physiology_of(170, run_lt1=130)  # nothing entered for the other sports

    assert threshold_for_sport(physiology, Sport.CYCLING) == 170
    assert lt1_for_sport(physiology, Sport.RUNNING) == 130
    assert lt1_for_sport(physiology, Sport.TRAIL) == 130
    assert lt1_for_sport(physiology, Sport.CYCLING) == round(170 * 0.83)  # not the running LT1
    split = physiology_of(170, other=140)
    assert lt1_for_sport(split, Sport.CYCLING) == round(140 * 0.83)
    assert lt1_for_sport(split, Sport.RUNNING) == round(170 * 0.83)


# --- Without heart rate, and a threshold that changes in the middle of the history -------------


@pytest.mark.parametrize("sport", SPORTS)
def test_an_activity_without_heart_rate_is_the_estimate_of_its_sport_with_no_zones(
    sport: Sport,
) -> None:
    physiology = physiology_of(170, other=150)

    load = compute_activity_load(sport, 3600.0, None, physiology)

    assert load.source is LoadSource.ESTIMATE
    assert load.zone_seconds.total == 0 and load.avg_bpm is None and load.max_bpm is None
    assert 0 < load.trimp_eq < 100
    assert domain_seconds(sport, None, physiology) is None


@pytest.mark.parametrize("seconds", [0.0, -5.0, math.inf, math.nan])
def test_a_load_with_a_moving_time_that_is_not_positive_and_finite_is_refused(
    seconds: float,
) -> None:
    with pytest.raises(ValueError, match="duration"):
        compute_activity_load(Sport.RUNNING, seconds, constant(150, 10), physiology_of(170))


def test_t_calc_a_threshold_that_changes_midway_applies_from_its_day_and_the_cache_is_not_stale(
    tmp_path: Path,
) -> None:
    world = make_world(tmp_path)
    try:
        days = [TODAY - timedelta(days=40 - 4 * n) for n in range(8)]
        for day in days:
            world.import_run(day, bpm=150)
        before = {a.start: a.load.trimp_eq for a in world.overview().activities}
        change = days[4]
        world.profile.session.physiology.put(
            PhysiologyEntry(change, physiology_of(150))
        )  # a lower threshold from day 5: the same 150 bpm is now a harder effort

        after = {a.start: a.load.trimp_eq for a in world.overview().activities}

        older = [a for a in after if a.date() < change]
        newer = [a for a in after if a.date() >= change]
        assert older and newer
        assert all(after[a] == before[a] for a in older)  # the history before is untouched
        assert all(after[a] > before[a] for a in newer)
        assert all(
            after[a] == pytest.approx(100 * 45 / 60, rel=0.01) for a in newer
        )  # 45 min at LT2
        fresh = make_world(tmp_path / "fresh", today=world.today)
        try:
            fresh.profile.session.physiology.put(PhysiologyEntry(change, physiology_of(150)))
            for day in days:
                fresh.import_run(day, bpm=150)
            scratch = {a.start: a.load.trimp_eq for a in fresh.overview().activities}
        finally:
            fresh.close()
        assert [after[k] for k in sorted(after)] == pytest.approx(
            [scratch[k] for k in sorted(scratch)]
        )
    finally:
        world.close()
