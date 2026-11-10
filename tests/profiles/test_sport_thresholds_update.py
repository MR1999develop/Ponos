# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Saving the cycling and other-activities threshold HR with the dated heart-rate measurements."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ponos_coach.load.domain import HeartRateLimits, ThresholdHeartRate
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementProblem,
    MeasurementUpdate,
    ThresholdReset,
)
from tests.profiles.test_measurements import TODAY, create_profile, entries, updater

DAY = TODAY - timedelta(days=10)


def test_an_update_with_only_a_sport_threshold_is_an_update() -> None:
    update = MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160))

    assert update.changes_heart_rate


def test_the_cycling_threshold_is_saved_on_the_chosen_day_and_keeps_the_other_values() -> None:
    session = create_profile()

    saved = updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160)))

    [new, today] = entries(session)
    assert new.valid_from == DAY
    assert new.physiology.bike_threshold == ThresholdHeartRate(160)
    assert new.physiology.other_threshold is None
    assert new.physiology.threshold == today.physiology.threshold
    assert today.physiology.bike_threshold is None  # the later entry is not touched
    assert saved.heart_rate is not None
    assert saved.heart_rate.valid_from == DAY
    assert saved.heart_rate.valid_to == TODAY - timedelta(days=1)


def test_the_other_threshold_is_saved_independently() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160)))

    updater(session)(MeasurementUpdate(DAY, other_threshold=ThresholdHeartRate(140)))

    [new, _] = entries(session)
    assert new.physiology.bike_threshold == ThresholdHeartRate(160)
    assert new.physiology.other_threshold == ThresholdHeartRate(140)


def test_a_value_set_earlier_carries_into_a_later_dated_entry() -> None:
    """An update from a later day starts from what was in force that day, optional ones included."""
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160)))
    later = TODAY - timedelta(days=2)

    updater(session)(MeasurementUpdate(later, max_bpm=195))

    in_force = {e.valid_from: e.physiology for e in entries(session)}[later]
    assert in_force.bike_threshold == ThresholdHeartRate(160)
    assert in_force.limits == HeartRateLimits(50, 195)


def test_reset_clears_the_threshold_back_to_the_default() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160)))

    updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdReset.DEFAULT))

    [new, _] = entries(session)
    assert new.physiology.bike_threshold is None


@pytest.mark.parametrize("bpm", [50, 191])
def test_a_sport_threshold_at_the_minimum_or_above_the_maximum_is_rejected_and_nothing_written(
    bpm: int,
) -> None:
    session = create_profile()
    before = entries(session)

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(bpm)))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE
    assert entries(session) == before


@pytest.mark.parametrize("bpm", [51, 190])
def test_the_limit_values_of_a_sport_threshold_are_accepted(bpm: int) -> None:
    session = create_profile()

    updater(session)(MeasurementUpdate(DAY, other_threshold=ThresholdHeartRate(bpm)))

    assert entries(session)[0].physiology.other_threshold == ThresholdHeartRate(bpm)


def test_raising_the_minimum_above_an_entered_sport_threshold_is_checked_on_the_result() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, bike_threshold=ThresholdHeartRate(160)))

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, min_bpm=160, threshold_bpm=170))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE
