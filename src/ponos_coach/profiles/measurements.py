# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Use cases for the profile measurements: weight, min HR, max HR, threshold HR and threshold pace,
each with a validity date.

- `UpdateMeasurements`: the saving of "Edit profile", in a single transaction: fixed data
  to complete (height and date of birth of a profile that lacks them), weight and dated HR
  measurements, and the threshold pace (a series of its own). Returns the effective validity
  period of what it saved.
- `ReadMeasurementHistory`: fixed data and history for "Edit profile".

These are health data: never in logs or error messages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.assessment.ports import ThresholdTestStore
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    ThresholdHeartRate,
    physiology_for_day,
)
from ponos_coach.load.ports import PhysiologyStore
from ponos_coach.profiles.domain import (
    MIN_MEASUREMENT_DATE,
    FixedProfileData,
    PaceInForce,
    ThresholdPace,
    ThresholdPaceEntry,
    Weight,
    WeightEntry,
    entries_of_tests,
    threshold_pace_in_force,
)
from ponos_coach.profiles.ports import FixedDataStore, ThresholdPaceStore, WeightStore


class MeasurementProblem(Enum):
    """Why a measurement update is rejected (decides the interface's message)."""

    NOTHING_TO_UPDATE = "nothing_to_update"  # no measurement or fixed data given
    DATE_IN_FUTURE = "date_in_future"  # the validity day is after today
    DATE_TOO_OLD = "date_too_old"  # the validity day is earlier than MIN_MEASUREMENT_DATE
    INVALID_HEART_RATE = "invalid_heart_rate"  # HR out of range or inconsistent


class InvalidMeasurementsError(ValueError):
    """Measurement update rejected. `problem` says why; never the values in the message."""

    def __init__(self, problem: MeasurementProblem) -> None:
        super().__init__(problem.value)
        self.problem = problem


@dataclass(frozen=True, slots=True)
class MeasurementUpdate:
    """New measurements valid from a day: at least one of weight, min HR, max HR, threshold HR and
    threshold pace.

    A `None` measurement does not change: the one in force that day applies. Sex is not changed.

    Raises:
        InvalidMeasurementsError: no measurement given (`NOTHING_TO_UPDATE`).
    """

    valid_from: date
    weight: Weight | None = None
    min_bpm: int | None = None
    max_bpm: int | None = None
    threshold_bpm: int | None = None
    threshold_pace: ThresholdPace | None = None

    def __post_init__(self) -> None:
        if self.weight is None and self.threshold_pace is None and not self.changes_heart_rate:
            raise InvalidMeasurementsError(MeasurementProblem.NOTHING_TO_UPDATE)

    @property
    def changes_heart_rate(self) -> bool:
        """True if the update touches min HR, max HR or threshold."""
        return not (self.min_bpm is None and self.max_bpm is None and self.threshold_bpm is None)


def _physiology_after(base: Physiology, update: MeasurementUpdate) -> Physiology:
    """The `base` parameters with the given measurements; the result must stay consistent."""
    try:
        return Physiology(
            HeartRateLimits(
                base.limits.min_bpm if update.min_bpm is None else update.min_bpm,
                base.limits.max_bpm if update.max_bpm is None else update.max_bpm,
            ),
            ThresholdHeartRate(
                base.threshold.bpm if update.threshold_bpm is None else update.threshold_bpm
            ),
            base.sex,
        )
    except ValueError as exc:
        raise InvalidMeasurementsError(MeasurementProblem.INVALID_HEART_RATE) from exc


@dataclass(frozen=True, slots=True)
class ValidityPeriod:
    """From when to when a saved measurement is valid.

    `valid_from` is the first day; `valid_to` the last day included, i.e. the day before the
    next already existing entry of the same series, or None if no entry follows it (it holds
    until the next measurement).
    """

    valid_from: date
    valid_to: date | None


@dataclass(frozen=True, slots=True)
class SavedMeasurements:
    """What `UpdateMeasurements` saved.

    The period of each series touched (None if not touched: weight, HR measurements and threshold
    pace are three distinct series) and whether it completed the fixed data.
    """

    weight: ValidityPeriod | None
    heart_rate: ValidityPeriod | None
    fixed_completed: bool
    threshold_pace: ValidityPeriod | None = None


def _period_in(valid_from: date, entry_days: Iterable[date]) -> ValidityPeriod:
    """The period of an entry with date `valid_from` among the entries of the series (itself
    included)."""
    following = min((day for day in entry_days if day > valid_from), default=None)
    return ValidityPeriod(valid_from, None if following is None else following - timedelta(days=1))


class UpdateMeasurements:
    """Saves the data of "Edit profile" in a single transaction: all or nothing.

    It can complete the fixed data (height and date of birth of a profile created before
    they existed; once set they are no longer changed) and add dated measurements: weight, min
    HR, max HR, threshold HR, threshold pace. If a part fails (for example the fixed data are
    already set) nothing remains written.

    Validity rule: a measurement holds from its day until the next already existing entry
    of the same series. A measurement with a past date therefore does not change what holds after a
    more
    recent entry, and for the same day the entry is replaced. Measurements not given remain
    those in force that day. The constraint `min HR < threshold <= max HR` is checked on the
    result. Loads already computed with different HR or threshold are recomputed on their own at
    the next
    overview (the fingerprint changes); body weight and the threshold pace do not enter the load.

    `today` (today's date in the profile's time zone) is injected by the Composition Root.
    """

    def __init__(
        self,
        activities: ActivityIndex,
        physiology: PhysiologyStore,
        weights: WeightStore,
        today: Callable[[], date],
        fixed_data: FixedDataStore,
        threshold_paces: ThresholdPaceStore,
    ) -> None:
        self._activities = activities
        self._physiology = physiology
        self._weights = weights
        self._today = today
        self._fixed = fixed_data
        self._paces = threshold_paces

    def __call__(
        self, update: MeasurementUpdate | None = None, fixed: FixedProfileData | None = None
    ) -> SavedMeasurements:
        """Saves the fixed data to complete (`fixed`) and/or the measurements of `update`, and says
        for
        which periods apply.

        Raises:
            InvalidMeasurementsError: nothing to save, day in the future or earlier than
                `MIN_MEASUREMENT_DATE`, or inconsistent HR; nothing was written.
            FixedDataAlreadySetError: the profile already has height and date of birth; nothing
            changes.
            MissingPhysiologyError: the profile has no physiological parameters to rely on.
            LoadStoreError, ProfileDataError: write failed; nothing changes.
        """
        self._check(update, fixed)
        with self._activities.transaction():
            return self._write(update, fixed)

    def save_in_open_transaction(
        self, update: MeasurementUpdate | None = None, fixed: FixedProfileData | None = None
    ) -> SavedMeasurements:
        """The same as calling the use case, inside a transaction the caller has already opened.

        For a use case that must save the measurements together with something else of its own,
        all or nothing, with the same rules (it does not open or close the transaction: if the
        caller's transaction fails, so do these writes). Same errors as the call.
        """
        self._check(update, fixed)
        return self._write(update, fixed)

    def _check(self, update: MeasurementUpdate | None, fixed: FixedProfileData | None) -> None:
        if update is None and fixed is None:
            raise InvalidMeasurementsError(MeasurementProblem.NOTHING_TO_UPDATE)
        if update is not None:
            if update.valid_from > self._today():
                raise InvalidMeasurementsError(MeasurementProblem.DATE_IN_FUTURE)
            if update.valid_from < MIN_MEASUREMENT_DATE:
                raise InvalidMeasurementsError(MeasurementProblem.DATE_TOO_OLD)

    def _write(
        self, update: MeasurementUpdate | None, fixed: FixedProfileData | None
    ) -> SavedMeasurements:
        if fixed is not None:
            self._fixed.set_once(fixed)
        weight_period, heart_rate_period, pace_period = self._save(update)
        return SavedMeasurements(weight_period, heart_rate_period, fixed is not None, pace_period)

    def _save(
        self, update: MeasurementUpdate | None
    ) -> tuple[ValidityPeriod | None, ValidityPeriod | None, ValidityPeriod | None]:
        """Writes the measurements and gives the periods (weight, HR, pace), inside the already
        open transaction."""
        if update is None:
            return (None, None, None)
        heart_rate_period = None
        if update.changes_heart_rate:
            base = physiology_for_day(self._physiology.list_entries(), update.valid_from)
            self._physiology.put(
                PhysiologyEntry(update.valid_from, _physiology_after(base, update))
            )
            heart_rate_period = _period_in(
                update.valid_from, (e.valid_from for e in self._physiology.list_entries())
            )
        weight_period = None
        if update.weight is not None:
            self._weights.put(WeightEntry(update.valid_from, update.weight))
            weight_period = _period_in(
                update.valid_from, (e.valid_from for e in self._weights.list_entries())
            )
        pace_period = None
        if update.threshold_pace is not None:
            self._paces.put(ThresholdPaceEntry(update.valid_from, update.threshold_pace))
            pace_period = _period_in(
                update.valid_from, (e.valid_from for e in self._paces.list_entries())
            )
        return (weight_period, heart_rate_period, pace_period)


@dataclass(frozen=True, slots=True)
class MeasurementHistory:
    """History for "Edit profile": fixed data (None if to complete), weight series, physiological
    parameters and threshold pace series, by ascending validity date, and the paces measured by
    the threshold tests (`test_paces`, dated on the test day)."""

    fixed: FixedProfileData | None
    weights: tuple[WeightEntry, ...]
    physiology: tuple[PhysiologyEntry, ...]
    threshold_paces: tuple[ThresholdPaceEntry, ...] = ()
    test_paces: tuple[ThresholdPaceEntry, ...] = ()

    def pace_in_force(self, day: date) -> PaceInForce | None:
        """The threshold pace the plan would use on `day`: the manual series and the paces of
        the threshold tests, the most recent wins (the manual one on the same day)."""
        return threshold_pace_in_force(self.threshold_paces, self.test_paces, day)


class ReadMeasurementHistory:
    """Reads the fixed data and the measurement history of the open profile."""

    def __init__(
        self,
        fixed: FixedDataStore,
        weights: WeightStore,
        physiology: PhysiologyStore,
        threshold_paces: ThresholdPaceStore,
        threshold_tests: ThresholdTestStore,
    ) -> None:
        self._fixed = fixed
        self._weights = weights
        self._physiology = physiology
        self._paces = threshold_paces
        self._tests = threshold_tests

    def __call__(self) -> MeasurementHistory:
        """The complete history.

        Raises:
            LoadStoreError, ProfileDataError, AssessmentStoreError: unreadable data.
        """
        return MeasurementHistory(
            self._fixed.get(),
            tuple(self._weights.list_entries()),
            tuple(self._physiology.list_entries()),
            tuple(self._paces.list_entries()),
            entries_of_tests(self._tests.list_tests()),
        )
