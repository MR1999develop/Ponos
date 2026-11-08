# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The vocabulary of the injury feature: closed sets, the pain scale and the ids.

Nothing here diagnoses. A zone is where it hurts, a warning sign is a reason to see a doctor, a step
is how much load the body is asked to take again (CSM1, CSM2, FER25: the project skill's table of
sources). The values of the enums are stable identifiers (they are saved).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum, IntEnum

_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_PAIN = 10


@dataclass(frozen=True, slots=True)
class InjuryId:
    """Stable identifier of an injury or an illness (1-64 letters, digits, `_` and `-`)."""

    value: str

    def __post_init__(self) -> None:
        if len(self.value) > 64 or not _ID.fullmatch(self.value):
            raise ValueError("an injury id is 1-64 letters, digits, '_' or '-'")


@dataclass(frozen=True, slots=True, order=True)
class PainScore:
    """Pain on the 0 (none) to 10 (worst imaginable) scale: a whole number, valid by design."""

    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or not 0 <= self.value <= MAX_PAIN:
            raise ValueError(f"pain is a whole number from 0 to {MAX_PAIN}")


class BodyZone(Enum):
    """Where it hurts. The user's words, not a diagnosis."""

    FOOT = "foot"  # includes the heel
    ANKLE = "ankle"
    ACHILLES = "achilles"
    CALF = "calf"
    SHIN = "shin"
    KNEE = "knee"
    THIGH = "thigh"
    HIP = "hip"  # includes the groin
    LOWER_BACK = "lower_back"
    UPPER_BODY = "upper_body"  # shoulder, arm, neck, chest wall


class Side(Enum):
    """Left, right or both (also for the zones that have no side, like the lower back)."""

    LEFT = "left"
    RIGHT = "right"
    BOTH = "both"


class Onset(Enum):
    """How it began."""

    SUDDEN = "sudden"
    GRADUAL = "gradual"


class PainFreeActivity(Enum):
    """An activity the user can do without pain (declared in the questionnaire)."""

    WALKING = "walking"
    CYCLING = "cycling"
    SWIMMING = "swimming"
    WATER_RUNNING = "water_running"


class Scope(Enum):
    """What a warning sign stops: the injured zone, or everything."""

    ZONE = "zone"
    WHOLE_PLAN = "whole_plan"


class WarningSign(Enum):
    """A symptom that deserves a doctor before any training. The app never says what it means."""

    NIGHT_PAIN = "night_pain"
    STRONG_REST_PAIN = "strong_rest_pain"  # derived from the rest pain, or ticked
    SWELLING_OR_DEFORMITY = "swelling_or_deformity"
    NUMBNESS_OR_WEAKNESS = "numbness_or_weakness"  # tingling, loss of strength
    CANNOT_BEAR_WEIGHT = "cannot_bear_weight"
    BONE_PAIN_WALKING_OR_NIGHT = "bone_pain_walking_or_night"  # localised, also when walking
    CALF_SWOLLEN_AND_WARM = "calf_swollen_and_warm"  # painful, constant, after rest or a journey
    CYCLING_STOP_PAIN = "cycling_stop_pain"  # forces a stop under effort, eases at once
    BLADDER_BOWEL_OR_SPREADING_WEAKNESS = "bladder_bowel_or_spreading_weakness"  # with back pain
    FEVER = "fever"
    GENERAL_MALAISE = "general_malaise"  # also unexplained loss of weight or appetite
    CHEST_PAIN = "chest_pain"
    PALPITATIONS = "palpitations"  # sudden, or with dizziness
    FAINTING = "fainting"  # any, during or after effort
    DISPROPORTIONATE_BREATHLESSNESS = "disproportionate_breathlessness"  # new, out of proportion


# Cardiac signs, fever and general malaise stop the whole plan (CSM2); the others the zone (CSM1).
WHOLE_PLAN_SIGNS = frozenset(
    {
        WarningSign.FEVER,
        WarningSign.GENERAL_MALAISE,
        WarningSign.CHEST_PAIN,
        WarningSign.PALPITATIONS,
        WarningSign.FAINTING,
        WarningSign.DISPROPORTIONATE_BREATHLESSNESS,
    }
)


def scope_of(sign: WarningSign) -> Scope:
    """What the sign stops."""
    return Scope.WHOLE_PLAN if sign in WHOLE_PLAN_SIGNS else Scope.ZONE


class IllnessKind(Enum):
    """The two groups of symptoms of "I do not feel well" (CSM2): above the neck with no fever, and
    fever, general malaise or aching muscles."""

    NECK_UP = "neck_up"
    SYSTEMIC = "systemic"


class Step(IntEnum):
    """A step of the path back. Always one at a time, never skipped."""

    ACTIVATION = 0  # light, short, frequent movement (FER25)
    WALKING = 1
    RUN_WALK = 2
    CONTINUOUS_EASY = 3
    VOLUME = 4
    INTENSITY = 5


FIRST_STEP = Step.ACTIVATION
LAST_STEP = Step.INTENSITY


class HaltReason(Enum):
    """Why the plan has no sessions at all. The value is a stable identifier."""

    ILLNESS = "illness"
    WHOLE_PLAN_SIGN = "whole_plan_sign"
    ZONE_SIGN = "zone_sign"
