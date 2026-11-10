# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Saving LT1 (both groups) and the other-sports LT2 with the dated heart-rate measurements."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest

from ponos_coach.load.domain import HeartRateLimits, ThresholdHeartRate
from ponos_coach.profiles.measurements import (
    InvalidMeasurementsError,
    MeasurementProblem,
    MeasurementUpdate,
    OptionalThresholdChange,
    ThresholdReset,
)
from tests.profiles.test_measurements import TODAY, create_profile, entries, updater

DAY = TODAY - timedelta(days=10)

# One update that sets a single optional value, by the name of the field.
_ONLY: dict[str, Callable[[OptionalThresholdChange], MeasurementUpdate]] = {
    "run_lt1": lambda value: MeasurementUpdate(DAY, run_lt1=value),
    "other_lt1": lambda value: MeasurementUpdate(DAY, other_lt1=value),
    "other_threshold": lambda value: MeasurementUpdate(DAY, other_threshold=value),
}


@pytest.mark.parametrize("field", ["run_lt1", "other_lt1", "other_threshold"])
def test_an_update_with_only_an_optional_value_is_an_update(field: str) -> None:
    update = _ONLY[field](ThresholdHeartRate(140))

    assert update.changes_heart_rate


def test_the_run_lt1_is_saved_on_the_chosen_day_and_keeps_the_other_values() -> None:
    session = create_profile()

    saved = updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(140)))

    [new, today] = entries(session)
    assert new.valid_from == DAY
    assert new.physiology.run_lt1 == ThresholdHeartRate(140)
    assert new.physiology.other_threshold is None
    assert new.physiology.other_lt1 is None
    assert new.physiology.threshold == today.physiology.threshold
    assert today.physiology.run_lt1 is None  # the later entry is not touched
    assert saved.heart_rate is not None
    assert saved.heart_rate.valid_from == DAY
    assert saved.heart_rate.valid_to == TODAY - timedelta(days=1)


def test_the_other_sports_values_are_saved_independently_of_the_running_ones() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(140)))

    updater(session)(
        MeasurementUpdate(
            DAY, other_threshold=ThresholdHeartRate(150), other_lt1=ThresholdHeartRate(120)
        )
    )

    [new, _] = entries(session)
    assert new.physiology.run_lt1 == ThresholdHeartRate(140)
    assert new.physiology.other_threshold == ThresholdHeartRate(150)
    assert new.physiology.other_lt1 == ThresholdHeartRate(120)


def test_a_value_set_earlier_carries_into_a_later_dated_entry() -> None:
    """An update from a later day starts from what was in force that day, optional ones included."""
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(140)))
    later = TODAY - timedelta(days=2)

    updater(session)(MeasurementUpdate(later, max_bpm=195))

    in_force = {e.valid_from: e.physiology for e in entries(session)}[later]
    assert in_force.run_lt1 == ThresholdHeartRate(140)
    assert in_force.limits == HeartRateLimits(50, 195)


@pytest.mark.parametrize("field", ["run_lt1", "other_lt1", "other_threshold"])
def test_reset_clears_the_value_back_to_the_default(field: str) -> None:
    session = create_profile()
    updater(session)(_ONLY[field](ThresholdHeartRate(140)))

    updater(session)(_ONLY[field](ThresholdReset.DEFAULT))

    [new, _] = entries(session)
    assert getattr(new.physiology, field) is None


@pytest.mark.parametrize("bpm", [50, 191])
def test_an_other_lt2_at_the_minimum_or_above_the_maximum_is_rejected_and_nothing_written(
    bpm: int,
) -> None:
    session = create_profile()
    before = entries(session)

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, other_threshold=ThresholdHeartRate(bpm)))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE
    assert entries(session) == before


@pytest.mark.parametrize("bpm", [51, 190])
def test_the_limit_values_of_an_other_lt2_are_accepted(bpm: int) -> None:
    session = create_profile()

    updater(session)(MeasurementUpdate(DAY, other_threshold=ThresholdHeartRate(bpm)))

    assert entries(session)[0].physiology.other_threshold == ThresholdHeartRate(bpm)


@pytest.mark.parametrize("bpm", [50, 170, 171])
def test_a_run_lt1_not_above_the_minimum_or_not_below_lt2_is_rejected(bpm: int) -> None:
    """The running LT2 of the test profile is 170 and its resting rate 50 (on and off points)."""
    session = create_profile()
    before = entries(session)

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(bpm)))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE
    assert entries(session) == before


@pytest.mark.parametrize("bpm", [51, 169])
def test_the_limit_values_of_a_run_lt1_are_accepted(bpm: int) -> None:
    session = create_profile()

    updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(bpm)))

    assert entries(session)[0].physiology.run_lt1 == ThresholdHeartRate(bpm)


def test_the_other_lt1_is_checked_against_the_other_lt2_of_the_same_update() -> None:
    session = create_profile()

    with pytest.raises(InvalidMeasurementsError):
        updater(session)(
            MeasurementUpdate(
                DAY, other_threshold=ThresholdHeartRate(150), other_lt1=ThresholdHeartRate(150)
            )
        )
    updater(session)(
        MeasurementUpdate(
            DAY, other_threshold=ThresholdHeartRate(150), other_lt1=ThresholdHeartRate(149)
        )
    )


def test_raising_the_minimum_to_an_entered_lt1_is_checked_on_the_result() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(140)))

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, min_bpm=140))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE


def test_lowering_lt2_to_an_entered_lt1_is_checked_on_the_result() -> None:
    session = create_profile()
    updater(session)(MeasurementUpdate(DAY, run_lt1=ThresholdHeartRate(150)))

    with pytest.raises(InvalidMeasurementsError) as raised:
        updater(session)(MeasurementUpdate(DAY, threshold_bpm=150))

    assert raised.value.problem is MeasurementProblem.INVALID_HEART_RATE
