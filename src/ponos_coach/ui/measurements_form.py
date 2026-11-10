# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "Edit profile": state and rules, testable without a window.

Measurements (weight, minimum HR, maximum HR, LT1 and LT2 for running and for other sports,
threshold pace) apply from a day
chosen by the user (today by default, never in the future). A field the user has not touched
shows, and has, the value in force that day; changing the day, untouched fields follow. A typed
value equal to the one in force is not a change: the field goes back to "untouched" and follows
the day. Only changed fields are saved; `changes` says what changes relative to the chosen day and
`pending_periods` for how long the new values would apply (a measurement applies until the next
already existing entry).
For a profile created before they existed, height and birth date are completed once.
The profile name is changed from here too: it is not a dated measurement and is saved together.

The form gives problems as categories (`EditIssue`): the view chooses the messages. Weight, height
and birth date are health data: never in a message or a log.
"""

from __future__ import annotations

import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    MissingPhysiologyError,
    Physiology,
    Sex,
    ThresholdHeartRate,
    default_lt1,
    lt1_for_sport,
    physiology_for_day,
    threshold_for_sport,
)
from ponos_coach.profiles.domain import (
    MIN_MEASUREMENT_DATE,
    FixedProfileData,
    InvalidProfileNameError,
    PaceInForce,
    ProfileName,
    ThresholdPace,
    Weight,
)
from ponos_coach.profiles.measurements import (
    MeasurementHistory,
    MeasurementProblem,
    MeasurementUpdate,
    OptionalThresholdChange,
    ThresholdReset,
    ValidityPeriod,
)
from ponos_coach.ui.body_input import (
    BodyProblem,
    read_birth_date,
    read_height,
    read_threshold_pace,
    read_weight,
)
from ponos_coach.ui.heart_rate_rules import (
    HeartRateProblem,
    heart_rate_problems,
    lt1_problems,
    optional_threshold_problems,
    read_optional_threshold,
)
from ponos_coach.ui.new_profile_form import DEFAULT_MAX_BPM, DEFAULT_MIN_BPM, estimated_threshold


class EditField(Enum):
    """Where the view shows a problem."""

    GENERAL = "general"
    NAME = "name"
    VALID_FROM = "valid_from"
    WEIGHT = "weight"
    MIN_BPM = "min_bpm"
    MAX_BPM = "max_bpm"
    THRESHOLD = "threshold"
    OTHER_THRESHOLD = "other_threshold"
    RUN_LT1 = "run_lt1"
    OTHER_LT1 = "other_lt1"
    THRESHOLD_PACE = "threshold_pace"
    HEIGHT = "height"
    BIRTH_DATE = "birth_date"


class NameProblem(Enum):
    """What is wrong with the new profile name (the field enforces the length limit)."""

    EMPTY = "empty"
    INVALID = "invalid"


type IssueKind = BodyProblem | HeartRateProblem | MeasurementProblem | NameProblem


@dataclass(frozen=True, slots=True)
class EditIssue:
    """A problem and the field it belongs to."""

    field: EditField
    kind: IssueKind


class MeasurementSeries(Enum):
    """The profile's three dated series: each has its own entries and validity periods."""

    WEIGHT = "weight"
    HEART_RATE = "heart_rate"
    THRESHOLD_PACE = "threshold_pace"


@dataclass(frozen=True, slots=True)
class FieldChange:
    """A value that saving would change, and the one that applied that day before."""

    field: EditField
    before: Weight | int | PaceInForce | None  # None: nothing recorded up to that day


@dataclass(frozen=True, slots=True)
class PendingPeriod:
    """For how long the new values of a series would apply if saved now."""

    series: MeasurementSeries
    period: ValidityPeriod

    @property
    def is_superseded(self) -> bool:
        """True if an already existing later entry takes the place of the new values."""
        return self.period.valid_to is not None


@dataclass(frozen=True, slots=True)
class MeasurementsRequest:
    """What saving must write: dated measurements, fixed data to complete and/or the new
    profile name."""

    update: MeasurementUpdate | None
    fixed: FixedProfileData | None
    new_name: ProfileName | None = None


_HEART_RATE_FIELD = {
    HeartRateProblem.MIN_NOT_BELOW_MAX: EditField.MIN_BPM,
    HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: EditField.THRESHOLD,
    HeartRateProblem.THRESHOLD_ABOVE_MAX: EditField.THRESHOLD,
}
# The optional values: the other-sports LT2 and the two LT1s (None in the profile: the default).
_OPTIONAL_FIELDS = (EditField.OTHER_THRESHOLD, EditField.RUN_LT1, EditField.OTHER_LT1)
_REJECTION_FIELD = {
    MeasurementProblem.NOTHING_TO_UPDATE: EditField.GENERAL,
    MeasurementProblem.DATE_IN_FUTURE: EditField.VALID_FROM,
    MeasurementProblem.DATE_TOO_OLD: EditField.VALID_FROM,
    MeasurementProblem.INVALID_HEART_RATE: EditField.MIN_BPM,
}


def issue_for_rejection(problem: MeasurementProblem) -> EditIssue:
    """The problem shown for an update rejected by the use case."""
    return EditIssue(_REJECTION_FIELD[problem], problem)


def weight_in_force(history: MeasurementHistory, day: date) -> Weight | None:
    """The weight in force on a day (the last entry not after it); `None` if there is none yet.

    Only to show the starting value: the entry that gets saved is decided by the use case.
    """
    entries = history.weights
    index = bisect_right([entry.valid_from for entry in entries], day) - 1
    return entries[index].weight if index >= 0 else None


def _default_physiology() -> Physiology:
    return Physiology(
        HeartRateLimits(DEFAULT_MIN_BPM, DEFAULT_MAX_BPM),
        ThresholdHeartRate(estimated_threshold(DEFAULT_MAX_BPM)),
        Sex.MALE,
    )


class MeasurementsForm:
    """Values of the "Edit profile" form and rules. It knows neither widgets nor texts."""

    def __init__(self, history: MeasurementHistory, today: date, name: str = "") -> None:
        self._history = history
        self._today = today
        self._current_name = name
        self._name_text: str | None = None  # None: untouched
        self._valid_from = today
        self._weight_text: str | None = None  # None: untouched
        self._min_bpm: int | None = None
        self._max_bpm: int | None = None
        self._threshold_bpm: int | None = None
        # None: untouched
        self._optional_texts: dict[EditField, str | None] = dict.fromkeys(_OPTIONAL_FIELDS)
        self._pace_text: str | None = None  # None: untouched
        self.height_text = ""  # only to complete the fixed data
        self.birth_date: date | None = None

    # --- Name -------------------------------------------------------------------------

    @property
    def current_name(self) -> str:
        """The profile name right now."""
        return self._current_name

    @property
    def name_text(self) -> str:
        """The name shown: the one typed or, if the field is untouched, the profile's."""
        return self._current_name if self._name_text is None else self._name_text

    def edit_name(self, text: str) -> None:
        """The user types the name. Equal to the current one (after NFC, no outer spaces): the
        field goes back to untouched."""
        same = unicodedata.normalize("NFC", text).strip() == self._current_name
        self._name_text = None if same else text

    def _name_reading(self) -> ProfileName | NameProblem | None:
        """The typed name if valid; the problem if not; `None` if untouched."""
        if self._name_text is None:
            return None
        try:
            return ProfileName.parse(self._name_text)
        except InvalidProfileNameError:
            return NameProblem.EMPTY if not self._name_text.strip() else NameProblem.INVALID

    # --- Fixed data -------------------------------------------------------------------

    @property
    def fixed(self) -> FixedProfileData | None:
        """The profile's height and birth date; `None` if they still need completing."""
        return self._history.fixed

    @property
    def needs_fixed_data(self) -> bool:
        """True for a profile created without height and birth date."""
        return self._history.fixed is None

    @property
    def sex(self) -> Sex:
        """The profile's sex (not changeable): the one from the last setting."""
        return self._latest_physiology().sex

    # --- Validity day -----------------------------------------------------------

    @property
    def today(self) -> date:
        """The upper limit of the validity day."""
        return self._today

    @property
    def valid_from(self) -> date:
        """The day from which the measurements apply (today by default)."""
        return self._valid_from

    def set_valid_from(self, day: date) -> None:
        """Choose the day; an out-of-range day is clamped to the nearest limit."""
        self._valid_from = min(max(day, MIN_MEASUREMENT_DATE), self._today)

    # --- Valori mostrati ---------------------------------------------------------------

    @property
    def baseline_weight(self) -> Weight | None:
        """The weight in force on the chosen day, if any."""
        return weight_in_force(self._history, self._valid_from)

    @property
    def edited_weight_text(self) -> str | None:
        """The weight typed by the user, `None` if the field was not touched."""
        return self._weight_text

    @property
    def min_bpm(self) -> int:
        """Minimum HR shown: the typed one, or the one in force on the chosen day."""
        return self._min_bpm if self._min_bpm is not None else self._baseline().limits.min_bpm

    @property
    def max_bpm(self) -> int:
        """Maximum HR shown."""
        return self._max_bpm if self._max_bpm is not None else self._baseline().limits.max_bpm

    @property
    def threshold_bpm(self) -> int:
        """Threshold shown."""
        if self._threshold_bpm is not None:
            return self._threshold_bpm
        return self._baseline().threshold.bpm

    def threshold_text(self, field: EditField) -> str:
        """The text of an optional value (other-sports LT2, an LT1): the one typed or, if the
        field is untouched, the one in force on the chosen day; empty if it is not set."""
        typed = self._optional_texts[field]
        if typed is not None:
            return typed
        baseline = self._optional_baseline(field)
        return "" if baseline is None else str(baseline.bpm)

    def threshold_default(self, field: EditField) -> int:
        """What an optional value is when left empty, for the heart rates now shown: the running
        LT2 for the other-sports LT2, the default share of the LT2 of its group for an LT1."""
        if field is EditField.OTHER_THRESHOLD:
            return threshold_for_sport(self._shown_physiology(), Sport.OTHER)
        return default_lt1(self._lt2_shown(field), self.min_bpm)

    def _lt2_shown(self, lt1_field: EditField) -> int:
        """The LT2 of the group of an LT1 field, as now shown (the other-sports one falls back to
        the running one when empty or not a number)."""
        if lt1_field is EditField.RUN_LT1:
            return self.threshold_bpm
        reading = read_optional_threshold(self.threshold_text(EditField.OTHER_THRESHOLD))
        return self.threshold_bpm if not isinstance(reading, ThresholdHeartRate) else reading.bpm

    def _shown_physiology(self) -> Physiology:
        """The heart rates shown with no optional value; the day's if the shown ones do not fit
        together (the form reports that separately)."""
        try:
            return Physiology(
                HeartRateLimits(self.min_bpm, self.max_bpm),
                ThresholdHeartRate(self.threshold_bpm),
                self._baseline().sex,
            )
        except ValueError:
            return replace(self._baseline(), other_threshold=None, run_lt1=None, other_lt1=None)

    def _optional_baseline(self, field: EditField) -> ThresholdHeartRate | None:
        base = self._baseline()
        return {
            EditField.OTHER_THRESHOLD: base.other_threshold,
            EditField.RUN_LT1: base.run_lt1,
            EditField.OTHER_LT1: base.other_lt1,
        }[field]

    @property
    def baseline_pace(self) -> PaceInForce | None:
        """The threshold pace in force on the chosen day, from the profile's settings or from the
        last threshold test (the more recent; the settings on the same day); `None` if neither."""
        return self._history.pace_in_force(self._valid_from)

    @property
    def edited_pace_text(self) -> str | None:
        """The pace typed by the user, `None` if the field was not touched."""
        return self._pace_text

    def _baseline(self) -> Physiology:
        try:
            return physiology_for_day(self._history.physiology, self._valid_from)
        except MissingPhysiologyError:
            return _default_physiology()

    def _latest_physiology(self) -> Physiology:
        try:
            return physiology_for_day(self._history.physiology, self._today)
        except MissingPhysiologyError:
            return _default_physiology()

    # --- Modifiche ---------------------------------------------------------------------

    def edit_weight(self, text: str) -> None:
        """The user types the weight. Empty or equal to the one in force: the field goes back to
        untouched
        and follows the chosen day."""
        reading = read_weight(text)
        untouched = reading is BodyProblem.WEIGHT_EMPTY or (
            not isinstance(reading, BodyProblem) and reading == self.baseline_weight
        )
        self._weight_text = None if untouched else text

    def edit_threshold_pace(self, text: str) -> None:
        """The user types the pace. Empty or equal to the one in force: the field goes back to
        untouched and follows the chosen day."""
        reading = read_threshold_pace(text)
        baseline = self.baseline_pace
        untouched = reading is BodyProblem.PACE_EMPTY or (
            isinstance(reading, ThresholdPace) and baseline is not None and reading == baseline.pace
        )
        self._pace_text = None if untouched else text

    def edit_min_bpm(self, value: int) -> None:
        """The user changes the minimum HR (equal to the one in force: goes back to untouched)."""
        self._min_bpm = _changed(value, self._baseline().limits.min_bpm)

    def edit_max_bpm(self, value: int) -> None:
        """The user changes the maximum HR (equal to the one in force: goes back to untouched)."""
        self._max_bpm = _changed(value, self._baseline().limits.max_bpm)

    def edit_threshold_bpm(self, value: int) -> None:
        """The user changes the threshold (equal to the one in force: goes back to untouched)."""
        self._threshold_bpm = _changed(value, self._baseline().threshold.bpm)

    def edit_other_threshold(self, text: str) -> None:
        """The user types the LT2 of the other sports; empty means the default (the running one).
        Equal to the one in force: the field goes back to untouched and follows the chosen day."""
        self._edit_optional(EditField.OTHER_THRESHOLD, text)

    def edit_run_lt1(self, text: str) -> None:
        """The user types the LT1 of running and trail; empty means the default."""
        self._edit_optional(EditField.RUN_LT1, text)

    def edit_other_lt1(self, text: str) -> None:
        """The user types the LT1 of the other sports; empty means the default."""
        self._edit_optional(EditField.OTHER_LT1, text)

    def _edit_optional(self, field: EditField, text: str) -> None:
        same = self._optional_change(field, text) is None
        self._optional_texts[field] = None if same else text

    def _optional_reading(self, text: str) -> OptionalThresholdChange | HeartRateProblem:
        """What the text says: empty is the default, a whole number of bpm in the threshold range
        is a threshold, anything else is a problem."""
        reading = read_optional_threshold(text)
        return ThresholdReset.DEFAULT if reading is None else reading

    def _optional_change(
        self, field: EditField, text: str | None
    ) -> OptionalThresholdChange | HeartRateProblem | None:
        """The change a text makes to an optional threshold relative to the one in force on the
        chosen day; `None` if it changes nothing (untouched, equal, or empty while unset)."""
        if text is None:
            return None
        reading = self._optional_reading(text)
        baseline = self._optional_baseline(field)
        if isinstance(reading, HeartRateProblem):
            return reading
        if reading is ThresholdReset.DEFAULT:
            return None if baseline is None else reading
        return None if reading == baseline else reading

    def _optional_changes(
        self,
    ) -> tuple[OptionalThresholdChange | HeartRateProblem | None, ...]:
        """The change of each optional value, in the order of `_OPTIONAL_FIELDS`."""
        return tuple(
            self._optional_change(field, self._optional_texts[field]) for field in _OPTIONAL_FIELDS
        )

    def _touches_heart_rate(self) -> bool:
        """True if saving changes any heart-rate value (they are saved as one entry)."""
        return any(v is not None for v in (*self._heart_rate_changes(), *self._optional_changes()))

    def _heart_rate_changes(self) -> tuple[int | None, int | None, int | None]:
        """The three HR values changed relative to the chosen day (`None` where unchanged)."""
        base = self._baseline()
        return (
            _changed(self._min_bpm, base.limits.min_bpm),
            _changed(self._max_bpm, base.limits.max_bpm),
            _changed(self._threshold_bpm, base.threshold.bpm),
        )

    def _fixed_started(self) -> bool:
        return bool(self.height_text.strip()) or self.birth_date is not None

    @property
    def has_changes(self) -> bool:
        """There is something to save (even a value to correct): enables "Save"."""
        heart_rate = self._touches_heart_rate()
        return (
            self._name_text is not None
            or self._weight_differs()
            or heart_rate
            or self._pace_differs()
            or (self.needs_fixed_data and self._fixed_started())
        )

    def _weight_differs(self) -> bool:
        """A typed weight that is not the one in force (even if it needs correcting)."""
        if self._weight_text is None:
            return False
        reading = read_weight(self._weight_text)
        return isinstance(reading, BodyProblem) or reading != self.baseline_weight

    def _pace_differs(self) -> bool:
        """A typed pace that is not the one in force (even if it needs correcting)."""
        if self._pace_text is None:
            return False
        reading = read_threshold_pace(self._pace_text)
        baseline = self.baseline_pace
        return isinstance(reading, BodyProblem) or baseline is None or reading != baseline.pace

    def changes(self) -> tuple[FieldChange, ...]:
        """The valid values that saving would change, with the one in force on the chosen day."""
        base = self._baseline()
        found: list[FieldChange] = []
        if self._changed_weight() is not None:
            found.append(FieldChange(EditField.WEIGHT, self.baseline_weight))
        for field, edited, before in (
            (EditField.MIN_BPM, self._min_bpm, base.limits.min_bpm),
            (EditField.MAX_BPM, self._max_bpm, base.limits.max_bpm),
            (EditField.THRESHOLD, self._threshold_bpm, base.threshold.bpm),
        ):
            if edited is not None and edited != before:
                found.append(FieldChange(field, before))
        in_force = {
            EditField.OTHER_THRESHOLD: threshold_for_sport(base, Sport.OTHER),
            EditField.RUN_LT1: lt1_for_sport(base, Sport.RUNNING),
            EditField.OTHER_LT1: lt1_for_sport(base, Sport.OTHER),
        }
        for field, change in zip(_OPTIONAL_FIELDS, self._optional_changes(), strict=True):
            if isinstance(change, ThresholdHeartRate | ThresholdReset):
                found.append(FieldChange(field, in_force[field]))
        if self._changed_pace() is not None:
            found.append(FieldChange(EditField.THRESHOLD_PACE, self.baseline_pace))
        return tuple(found)

    def pending_periods(self) -> tuple[PendingPeriod, ...]:
        """For how long the new values of each touched series would apply: from the chosen day up to
        the one before the next already existing entry (open if there is none)."""
        found: list[PendingPeriod] = []
        if self._changed_weight() is not None:
            days = [entry.valid_from for entry in self._history.weights]
            found.append(PendingPeriod(MeasurementSeries.WEIGHT, self._period_in(days)))
        if self._touches_heart_rate():
            days = [entry.valid_from for entry in self._history.physiology]
            found.append(PendingPeriod(MeasurementSeries.HEART_RATE, self._period_in(days)))
        if self._changed_pace() is not None:
            days = [entry.valid_from for entry in self._history.threshold_paces]
            found.append(PendingPeriod(MeasurementSeries.THRESHOLD_PACE, self._period_in(days)))
        return tuple(found)

    def _period_in(self, entry_days: list[date]) -> ValidityPeriod:
        following = min((day for day in entry_days if day > self._valid_from), default=None)
        return ValidityPeriod(
            self._valid_from, None if following is None else following - timedelta(days=1)
        )

    # --- Validazione -------------------------------------------------------------------

    def issues(self) -> tuple[EditIssue, ...]:
        """Problems of the current values, in field order (empty if it can be saved)."""
        found: list[EditIssue] = []
        name = self._name_reading()
        if isinstance(name, NameProblem):
            found.append(EditIssue(EditField.NAME, name))
        if self._weight_text is not None:
            reading = read_weight(self._weight_text)
            if isinstance(reading, BodyProblem):
                found.append(EditIssue(EditField.WEIGHT, reading))
        if any(value is not None for value in self._heart_rate_changes()):
            found.extend(
                EditIssue(_HEART_RATE_FIELD[kind], kind)
                for kind in heart_rate_problems(self.min_bpm, self.max_bpm, self.threshold_bpm)
            )
        found.extend(self._optional_threshold_issues())
        found.extend(self._pace_issues())
        if self.needs_fixed_data and self._fixed_started():
            found.extend(self._fixed_issues())
        return tuple(found)

    def _optional_threshold_issues(self) -> list[EditIssue]:
        """Problems of the optional values: a text that is not a threshold, or a value (typed, or
        in force when the heart rates or the LT2 change) outside its range: the other-sports LT2
        between the resting and the maximum rate, an LT1 between the resting rate and its LT2."""
        found: list[EditIssue] = []
        heart_rate_changed = any(v is not None for v in self._heart_rate_changes())
        anything_typed = any(text is not None for text in self._optional_texts.values())
        for field in _OPTIONAL_FIELDS:
            typed = self._optional_texts[field]
            reading = None if typed is None else self._optional_reading(typed)
            if isinstance(reading, HeartRateProblem):
                found.append(EditIssue(field, reading))
                continue
            is_lt1 = field is not EditField.OTHER_THRESHOLD
            if typed is None and not (heart_rate_changed or (is_lt1 and anything_typed)):
                continue
            shown = self.threshold_text(field)
            if not shown:
                continue
            if is_lt1:
                kinds = lt1_problems(self.min_bpm, int(shown), self._lt2_shown(field))
            else:
                kinds = optional_threshold_problems(self.min_bpm, self.max_bpm, int(shown))
            found.extend(EditIssue(field, kind) for kind in kinds)
        return found

    def _pace_issues(self) -> list[EditIssue]:
        if self._pace_text is None:
            return []
        pace = read_threshold_pace(self._pace_text)
        return [EditIssue(EditField.THRESHOLD_PACE, pace)] if isinstance(pace, BodyProblem) else []

    def _fixed_issues(self) -> list[EditIssue]:
        height = read_height(self.height_text)
        birth = read_birth_date(self.birth_date, self._today)
        found: list[EditIssue] = []
        if isinstance(height, BodyProblem):
            found.append(EditIssue(EditField.HEIGHT, height))
        if isinstance(birth, BodyProblem):
            found.append(EditIssue(EditField.BIRTH_DATE, birth))
        return found

    def build(self) -> MeasurementsRequest | tuple[EditIssue, ...]:
        """The save request with only the changed fields, or the problems that prevent it."""
        found = self.issues()
        if found:
            return found
        weight = self._changed_weight()
        pace = self._changed_pace()
        min_bpm, max_bpm, threshold_bpm = self._heart_rate_changes()
        other, run_lt1, other_lt1 = (
            None if isinstance(c, HeartRateProblem) else c for c in self._optional_changes()
        )
        update = None
        if (
            weight is not None
            or pace is not None
            or any(
                v is not None for v in (min_bpm, max_bpm, threshold_bpm, other, run_lt1, other_lt1)
            )
        ):
            update = MeasurementUpdate(
                self._valid_from,
                weight,
                min_bpm,
                max_bpm,
                threshold_bpm,
                pace,
                other_threshold=other,
                run_lt1=run_lt1,
                other_lt1=other_lt1,
            )
        fixed = self._built_fixed()
        name = self._name_reading()
        new_name = name if isinstance(name, ProfileName) else None
        if update is None and fixed is None and new_name is None:
            return (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)
        return MeasurementsRequest(update, fixed, new_name)

    def _changed_weight(self) -> Weight | None:
        """The typed weight, if valid and different from the one in force on the chosen day."""
        if self._weight_text is None:
            return None
        reading = read_weight(self._weight_text)
        if isinstance(reading, BodyProblem) or reading == self.baseline_weight:
            return None
        return reading

    def _changed_pace(self) -> ThresholdPace | None:
        """The typed pace, if valid and different from the one in force on the chosen day."""
        if self._pace_text is None:
            return None
        reading = read_threshold_pace(self._pace_text)
        baseline = self.baseline_pace
        if isinstance(reading, BodyProblem) or (baseline is not None and reading == baseline.pace):
            return None
        return reading

    def _built_fixed(self) -> FixedProfileData | None:
        if not (self.needs_fixed_data and self._fixed_started()):
            return None
        height = read_height(self.height_text)
        birth = read_birth_date(self.birth_date, self._today)
        if isinstance(height, BodyProblem) or isinstance(birth, BodyProblem):
            return None
        return FixedProfileData(height, birth)


def _changed(edited: int | None, base: int) -> int | None:
    """The typed value if it differs from the one in force, otherwise `None`."""
    return None if edited is None or edited == base else edited
