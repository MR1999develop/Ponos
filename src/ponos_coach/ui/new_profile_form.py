# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "New profile": state and validation, testable without a window.

The model holds the values, decides where the data goes (the chosen folder, or a subfolder with
the profile name if the chosen one already contains something else), estimates the threshold HR
from the maximum
until the user sets it, and says which fields have a problem (categories, not texts: the
view chooses the messages). It gives a `NewProfileRequest` only if everything is valid.

Height, weight and birth date are health data: they stay in the form as text or date, are
read with `body_input` and never appear in a message or a log.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from enum import Enum
from pathlib import Path

from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.profiles.domain import (
    NAME_MAX_CHARS,
    BirthDate,
    FixedProfileData,
    FolderState,
    Height,
    InvalidProfileNameError,
    ProfileName,
    Weight,
)
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.body_input import BodyProblem, read_birth_date, read_height, read_weight
from ponos_coach.ui.heart_rate_rules import (
    MAX_ALLOWED_BPM,
    MIN_ALLOWED_BPM,
    MIN_THRESHOLD_BPM,
    HeartRateProblem,
    heart_rate_problems,
)

__all__ = [
    "DEFAULT_MAX_BPM",
    "DEFAULT_MIN_BPM",
    "MAX_ALLOWED_BPM",
    "MIN_ALLOWED_BPM",
    "MIN_THRESHOLD_BPM",
    "RESERVED_NAME_SUFFIX",
    "THRESHOLD_SHARE_OF_MAX",
    "Field",
    "NewProfileForm",
    "Problem",
    "body_problem_of",
    "estimated_threshold",
    "folder_name_from",
    "heart_rate_problem_of",
]

DEFAULT_MIN_BPM = 50
DEFAULT_MAX_BPM = 190
THRESHOLD_SHARE_OF_MAX = 0.9  # estimated threshold: 90% of the maximum HR

_FORBIDDEN_IN_FOLDER_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Names that Windows reserves for devices, even with an extension (CON.txt); COM and LPT with
# superscript digits are reserved in the same way.
_RESERVED_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"}
)
RESERVED_NAME_SUFFIX = "_profile"


class Field(Enum):
    """Form field."""

    NAME = "name"
    FOLDER = "folder"
    HEIGHT = "height"
    WEIGHT = "weight"
    BIRTH_DATE = "birth_date"
    MIN_BPM = "min_bpm"
    MAX_BPM = "max_bpm"
    THRESHOLD = "threshold"


class Problem(Enum):
    """What is wrong with a field."""

    NAME_EMPTY = (Field.NAME, "name_empty")
    NAME_TOO_LONG = (Field.NAME, "name_too_long")
    NAME_INVALID = (Field.NAME, "name_invalid")
    FOLDER_EMPTY = (Field.FOLDER, "folder_empty")
    FOLDER_NOT_ABSOLUTE = (Field.FOLDER, "folder_not_absolute")
    HEIGHT_EMPTY = (Field.HEIGHT, "height_empty")
    HEIGHT_INVALID = (Field.HEIGHT, "height_invalid")
    WEIGHT_EMPTY = (Field.WEIGHT, "weight_empty")
    WEIGHT_INVALID = (Field.WEIGHT, "weight_invalid")
    BIRTH_EMPTY = (Field.BIRTH_DATE, "birth_empty")
    BIRTH_INVALID = (Field.BIRTH_DATE, "birth_invalid")
    MIN_NOT_BELOW_MAX = (Field.MIN_BPM, "min_not_below_max")
    THRESHOLD_NOT_ABOVE_MIN = (Field.THRESHOLD, "threshold_not_above_min")
    THRESHOLD_ABOVE_MAX = (Field.THRESHOLD, "threshold_above_max")

    @property
    def field(self) -> Field:
        """The field the problem belongs to."""
        return self.value[0]


_BODY_PROBLEMS = {
    BodyProblem.HEIGHT_EMPTY: Problem.HEIGHT_EMPTY,
    BodyProblem.HEIGHT_INVALID: Problem.HEIGHT_INVALID,
    BodyProblem.WEIGHT_EMPTY: Problem.WEIGHT_EMPTY,
    BodyProblem.WEIGHT_INVALID: Problem.WEIGHT_INVALID,
    BodyProblem.BIRTH_EMPTY: Problem.BIRTH_EMPTY,
    BodyProblem.BIRTH_INVALID: Problem.BIRTH_INVALID,
}
_PROBLEM_TO_BODY = {problem: body for body, problem in _BODY_PROBLEMS.items()}
_HEART_RATE_PROBLEMS = {
    HeartRateProblem.MIN_NOT_BELOW_MAX: Problem.MIN_NOT_BELOW_MAX,
    HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: Problem.THRESHOLD_NOT_ABOVE_MIN,
    HeartRateProblem.THRESHOLD_ABOVE_MAX: Problem.THRESHOLD_ABOVE_MAX,
}
_PROBLEM_TO_HEART_RATE = {problem: rule for rule, problem in _HEART_RATE_PROBLEMS.items()}

type BodyReadings = tuple[
    Height | BodyProblem, Weight | BodyProblem, BirthDate | BodyProblem
]  # height, weight and birth date read only once


def heart_rate_problem_of(problem: Problem) -> HeartRateProblem | None:
    """The `heart_rate_rules` rule violated by an HR problem."""
    return _PROBLEM_TO_HEART_RATE.get(problem)


def body_problem_of(problem: Problem) -> BodyProblem | None:
    """The `body_input` category of a height, weight or birth date problem."""
    return _PROBLEM_TO_BODY.get(problem)


def folder_name_from(profile_name: str) -> str:
    """Folder name derived from a profile name.

    Forbidden characters become `_`; a name that Windows reserves for a device (CON, PRN,
    AUX, NUL, COM1-9, LPT1-9, even with an extension) gets the suffix `_profile` before
    the extension.
    """
    cleaned = _FORBIDDEN_IN_FOLDER_NAME.sub("_", unicodedata.normalize("NFC", profile_name))
    cleaned = cleaned.strip().rstrip(". ")
    stem, dot, extension = cleaned.partition(".")
    if stem.rstrip(" ").upper() in _RESERVED_DEVICE_NAMES:
        return f"{stem}{RESERVED_NAME_SUFFIX}{dot}{extension}"
    return cleaned


def estimated_threshold(max_bpm: int) -> int:
    """Threshold HR estimated from the maximum (90%), brought within the allowed limits."""
    return max(MIN_THRESHOLD_BPM, min(MAX_ALLOWED_BPM, round(max_bpm * THRESHOLD_SHARE_OF_MAX)))


class NewProfileForm:
    """Form values and rules. It knows neither widgets nor texts."""

    def __init__(self, timezone_name: str) -> None:
        self.timezone_name = timezone_name
        self.name = ""
        self.height_text = ""  # empty until the user types them: no suggestion
        self.weight_text = ""
        self.birth_date: date | None = None
        self.min_bpm = DEFAULT_MIN_BPM
        self.max_bpm = DEFAULT_MAX_BPM
        self.sex = Sex.MALE
        self._folder_text = ""  # empty until the user chooses: no suggestion
        self._threshold: int | None = None  # None: estimated from the maximum
        self.folder_state: FolderState | None = None  # last known examination of the folder

    # --- Folder and threshold ------------------------------------------------------------

    @property
    def folder_text(self) -> str:
        """Folder chosen by the user (empty until chosen)."""
        return self._folder_text

    def choose_folder(self, text: str) -> None:
        """The user chooses or types the folder; the previous examination no longer applies."""
        self._folder_text = text
        self.folder_state = None

    @property
    def uses_subfolder(self) -> bool:
        """True if the chosen folder already contains something else: the data goes in a
        subfolder."""
        return self.folder_state is FolderState.NOT_EMPTY

    @property
    def destination(self) -> str:
        """Folder where the data will go (empty if not yet determinable)."""
        chosen = self._folder_text.strip()
        if not chosen or not self.uses_subfolder:
            return chosen
        sub = folder_name_from(unicodedata.normalize("NFC", self.name).strip())
        return str(Path(chosen) / sub) if sub else ""

    @property
    def threshold_bpm(self) -> int:
        """Threshold HR: the one set, or the estimate from the maximum."""
        return estimated_threshold(self.max_bpm) if self._threshold is None else self._threshold

    @property
    def threshold_is_estimate(self) -> bool:
        """True while the threshold is an estimate to confirm with the test."""
        return self._threshold is None

    def set_threshold(self, bpm: int) -> None:
        """The user sets the threshold: it is no longer an estimate."""
        self._threshold = bpm

    # --- Validazione ------------------------------------------------------------------

    def problems(self, today: date) -> tuple[Problem, ...]:
        """Problems of the current values, in field order (empty if everything is valid).

        `today` (today's date in the profile's time zone) serves for the birth date limit.
        """
        return self._problems_of(self._read_body(today))

    def _read_body(self, today: date) -> BodyReadings:
        """Height, weight and birth date: valid values or the problem category."""
        return (
            read_height(self.height_text),
            read_weight(self.weight_text),
            read_birth_date(self.birth_date, today),
        )

    def _problems_of(self, readings: BodyReadings) -> tuple[Problem, ...]:
        found = [
            *self._name_problems(),
            *self._folder_problems(),
            *(_BODY_PROBLEMS[item] for item in readings if isinstance(item, BodyProblem)),
            *self._heart_rate_problems(),
        ]
        return tuple(found)

    def _name_problems(self) -> list[Problem]:
        text = unicodedata.normalize("NFC", self.name).strip()
        if not text:
            return [Problem.NAME_EMPTY]
        if len(text) > NAME_MAX_CHARS:
            return [Problem.NAME_TOO_LONG]
        try:
            ProfileName.parse(self.name)
        except InvalidProfileNameError:
            return [Problem.NAME_INVALID]
        return []

    def folder_problem(self) -> Problem | None:
        """Problem of the chosen folder (the only one that blocks "Create profile" from the
        start)."""
        text = self._folder_text.strip()
        if not text:
            return Problem.FOLDER_EMPTY
        try:
            absolute = Path(text).is_absolute()
        except (ValueError, OSError):
            absolute = False
        return None if absolute else Problem.FOLDER_NOT_ABSOLUTE

    def _folder_problems(self) -> list[Problem]:
        found = self.folder_problem()
        if found is not None:
            return [found]
        if self.uses_subfolder and not self.destination and not self._name_problems():
            return [Problem.NAME_INVALID]  # valid name but without a usable folder name
        return []

    def _heart_rate_problems(self) -> list[Problem]:
        found = heart_rate_problems(self.min_bpm, self.max_bpm, self.threshold_bpm)
        return [_HEART_RATE_PROBLEMS[item] for item in found]

    def build_request(self, today: date) -> NewProfileRequest | tuple[Problem, ...]:
        """The creation request, or the problems that prevent it."""
        readings = self._read_body(today)
        height, weight, birth = readings
        found = self._problems_of(readings)
        if (
            found
            or isinstance(height, BodyProblem)
            or isinstance(weight, BodyProblem)
            or isinstance(birth, BodyProblem)
        ):
            return found
        physiology = Physiology(
            HeartRateLimits(self.min_bpm, self.max_bpm),
            ThresholdHeartRate(self.threshold_bpm),
            self.sex,
        )
        return NewProfileRequest(
            ProfileName.parse(self.name),
            Path(self.destination),
            self.timezone_name,
            physiology,
            today,
            FixedProfileData(height, birth),
            weight,
        )
