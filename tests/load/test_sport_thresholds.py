# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Threshold heart rate per sport group: running, cycling and other activities.

Acceptance: entering a cycling threshold changes the load and the zones of a bike activity from
the validity day on, and a recalculation from a partial cache equals one from scratch (T-CALC-05).
Domain: defaults, limits (on and off points), the single decision point and the fingerprint.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import numpy as np
import pytest

from ponos_coach.activities.domain import (
    ActivityOrigin,
    ActivityRecord,
    FileFormat,
    Sport,
)
from ponos_coach.activities.fit_decoder import FitLimits, decode_activity
from ponos_coach.load.activity_detail import ActivityDetail, GetActivityDetail
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    SportGroup,
    ThresholdHeartRate,
    equivalence_factor,
    load_fingerprint,
    sport_group,
    threshold_for_sport,
    zone_starts_bpm,
)
from tests.builders import IMPORTED_AT, physiology, physiology_entry
from tests.fixtures.generators import fit_writer as fw
from tests.load.test_profile_overview import (
    CHANGE_DAY,
    FIRST_PARAMS_DAY,
    ROME,
    World,
    decode_fit,
)

LIMITS = HeartRateLimits(50, 190)


def with_thresholds(
    run: int = 170, bike: int | None = None, other: int | None = None
) -> Physiology:
    return Physiology(
        LIMITS,
        ThresholdHeartRate(run),
        Sex.MALE,
        None if bike is None else ThresholdHeartRate(bike),
        None if other is None else ThresholdHeartRate(other),
    )


# --- Groups and defaults -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sport", "group"),
    [
        (Sport.RUNNING, SportGroup.RUNNING),
        (Sport.TRAIL, SportGroup.RUNNING),
        (Sport.CYCLING, SportGroup.CYCLING),
        (Sport.WALKING, SportGroup.OTHER),
        (Sport.HIKING, SportGroup.OTHER),
        (Sport.STRENGTH, SportGroup.OTHER),
        (Sport.OTHER, SportGroup.OTHER),
    ],
)
def test_every_sport_belongs_to_one_group(sport: Sport, group: SportGroup) -> None:
    assert sport_group(sport) is group


def test_every_sport_has_a_group() -> None:
    assert {sport_group(s) for s in Sport} == set(SportGroup)


def test_without_entered_values_the_defaults_of_before_apply() -> None:
    p = with_thresholds(170)

    assert threshold_for_sport(p, Sport.CYCLING) == 166
    assert [threshold_for_sport(p, s) for s in Sport if s is not Sport.CYCLING] == [170] * (
        len(Sport) - 1
    )


def test_an_entered_cycling_threshold_applies_to_cycling_only() -> None:
    p = with_thresholds(170, bike=160)

    assert threshold_for_sport(p, Sport.CYCLING) == 160
    assert threshold_for_sport(p, Sport.RUNNING) == 170
    assert threshold_for_sport(p, Sport.WALKING) == 170


@pytest.mark.parametrize("sport", [Sport.WALKING, Sport.HIKING, Sport.STRENGTH, Sport.OTHER])
def test_an_entered_other_threshold_applies_to_every_sport_that_is_neither_run_nor_bike(
    sport: Sport,
) -> None:
    p = with_thresholds(170, other=140)

    assert threshold_for_sport(p, sport) == 140
    assert threshold_for_sport(p, Sport.TRAIL) == 170
    assert threshold_for_sport(p, Sport.CYCLING) == 166


def test_the_other_threshold_does_not_change_the_cycling_default() -> None:
    assert threshold_for_sport(with_thresholds(170, other=140), Sport.CYCLING) == 166


def test_the_equivalence_factor_follows_the_entered_threshold() -> None:
    p = with_thresholds(170, bike=150)

    assert equivalence_factor(p, Sport.CYCLING) == pytest.approx(120 / 100)


# --- Limits: the same as the running threshold, on and off points --------------------------------


@pytest.mark.parametrize("bpm", [51, 120, 190])
@pytest.mark.parametrize("which", ["bike", "other"])
def test_an_optional_threshold_just_above_the_minimum_up_to_the_maximum_is_accepted(
    which: str, bpm: int
) -> None:
    p = with_thresholds(170, **{which: bpm})

    assert threshold_for_sport(p, Sport.CYCLING if which == "bike" else Sport.OTHER) == bpm


@pytest.mark.parametrize("bpm", [50, 49, 191])
@pytest.mark.parametrize("which", ["bike", "other"])
def test_an_optional_threshold_at_the_minimum_or_outside_the_limits_is_rejected(
    which: str, bpm: int
) -> None:
    with pytest.raises(ValueError, match="threshold"):
        with_thresholds(170, **{which: bpm})


@pytest.mark.parametrize("bpm", [39, 251])
def test_an_optional_threshold_outside_40_to_250_is_not_a_threshold(bpm: int) -> None:
    with pytest.raises(ValueError, match="threshold"):
        ThresholdHeartRate(bpm)


# --- Fingerprint -------------------------------------------------------------------------------


def test_the_fingerprint_changes_with_an_entered_threshold_that_differs_from_the_default() -> None:
    base = load_fingerprint(with_thresholds(170))

    assert load_fingerprint(with_thresholds(170, bike=160)) != base
    assert load_fingerprint(with_thresholds(170, other=150)) != base
    assert load_fingerprint(with_thresholds(170, bike=160)) != load_fingerprint(
        with_thresholds(170, other=160)
    )


def test_a_profile_that_never_set_them_keeps_the_fingerprint_it_had() -> None:
    assert load_fingerprint(with_thresholds(170)) == load_fingerprint(
        Physiology(LIMITS, ThresholdHeartRate(170), Sex.MALE)
    )


def test_entering_the_value_the_default_would_give_keeps_the_loads_valid() -> None:
    base = load_fingerprint(with_thresholds(170))

    assert load_fingerprint(with_thresholds(170, bike=166)) == base
    assert load_fingerprint(with_thresholds(170, other=170)) == base


# --- Acceptance: a cycling threshold changes the load from its validity day ----------------------


def bike_bytes(start: datetime, serial: int, bpm: int = 150, minutes: int = 30) -> bytes:
    first = fw.datetime_to_fit(start)
    samples = [fw.Sample(first + i, bpm) for i in range(minutes * 60)]
    spec = fw.ActivitySpec(
        start, fw.Sport.CYCLING, fw.SubSport.GENERIC, samples, serial_number=serial
    )
    return fw.build_activity(spec)


def add_ride(world: World, start: datetime, serial: int) -> ActivityRecord:
    data = bike_bytes(start, serial)
    activity = decode_activity(data, FitLimits())
    archived = world.archive.store(data, FileFormat.FIT, activity.start)
    record = ActivityRecord.from_activity(activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT)
    world.profile.activities.add(record)
    world.records.append(record)
    return record


def two_rides(world: World) -> tuple[ActivityRecord, ActivityRecord]:
    before = add_ride(world, datetime(2026, 1, 15, 7, 0, tzinfo=UTC), serial=1)
    after = add_ride(world, datetime(2026, 2, 10, 7, 0, tzinfo=UTC), serial=2)
    return before, after


def put_cycling_threshold_from(world: World, day: date, bike: int | None) -> None:
    base = physiology()
    entry = PhysiologyEntry(
        day,
        Physiology(
            base.limits,
            base.threshold,
            base.sex,
            None if bike is None else ThresholdHeartRate(bike),
        ),
    )
    world.profile.physiology.put(entry)


def test_a_cycling_threshold_changes_the_load_and_zones_of_rides_from_its_day_on() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_rides(world)
    plain = world.overview()

    put_cycling_threshold_from(world, CHANGE_DAY, 150)
    changed = world.overview()

    (ride_before, ride_after) = plain.activities
    (new_before, new_after) = changed.activities
    assert new_before.load == ride_before.load  # before the validity day: unchanged
    assert new_after.load != ride_after.load
    assert new_after.load.trimp_eq > ride_after.load.trimp_eq  # a lower threshold: more load
    assert new_after.load.zone_seconds != ride_after.load.zone_seconds


def test_a_ride_at_the_entered_threshold_for_an_hour_is_worth_one_hundred() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    put_cycling_threshold_from(world, FIRST_PARAMS_DAY, 150)  # the ride's heart rate is 150
    data = bike_bytes(datetime(2026, 1, 15, 7, 0, tzinfo=UTC), 1, bpm=150, minutes=60)
    activity = decode_activity(data, FitLimits())
    archived = world.archive.store(data, FileFormat.FIT, activity.start)
    world.profile.activities.add(
        ActivityRecord.from_activity(activity, archived, ActivityOrigin.IMPORT, IMPORTED_AT)
    )

    (entry,) = world.overview().activities

    assert entry.load.trimp_eq == pytest.approx(100.0, rel=1e-3)


def test_recalculation_after_entering_the_threshold_equals_calculation_from_scratch() -> None:
    """T-CALC-05 with the new threshold: partial cache, full recomputation, from scratch."""
    incremental = World()
    incremental.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_rides(incremental)
    incremental.overview()
    put_cycling_threshold_from(incremental, CHANGE_DAY, 150)
    after_change = incremental.overview()  # the first ride stays in cache

    from_scratch = World()
    from_scratch.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    put_cycling_threshold_from(from_scratch, CHANGE_DAY, 150)
    two_rides(from_scratch)
    expected = from_scratch.overview()

    incremental.profile.load_cache.clear()
    full = incremental.overview()

    assert after_change.activities == expected.activities
    assert full.activities == expected.activities
    assert np.array_equal(after_change.pmc.ctl, expected.pmc.ctl)
    assert np.array_equal(full.pmc.atl, expected.pmc.atl)


def test_clearing_the_cycling_threshold_gives_back_the_default_load() -> None:
    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    two_rides(world)
    default = world.overview()

    put_cycling_threshold_from(world, FIRST_PARAMS_DAY, 150)
    world.overview()
    put_cycling_threshold_from(world, FIRST_PARAMS_DAY, None)

    assert world.overview().activities == default.activities


def test_the_detail_of_a_ride_shows_the_zones_of_the_cycling_threshold() -> None:

    world = World()
    world.profile.physiology.put(physiology_entry(FIRST_PARAMS_DAY))
    put_cycling_threshold_from(world, FIRST_PARAMS_DAY, 150)
    record = add_ride(world, datetime(2026, 2, 10, 7, 0, tzinfo=UTC), serial=1)

    found = GetActivityDetail(
        world.profile.activities,
        world.archive,
        world.profile.physiology,
        decode_fit,
        timezone=ROME,
        max_file_bytes=50_000_000,
    )(record.identity)

    assert isinstance(found, ActivityDetail)
    assert found.threshold_bpm == 150
    assert found.zone_starts_bpm == zone_starts_bpm(150)
