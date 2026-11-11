# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Every number of the injury feature, in one place.

Sources, by the codes of the project skill's table: CSM1 and CSM2 (Clinical Sports Medicine, vol. 1
and 2), SIL (the skill's rule of the path), FER25 (the activation step). "Project default" means
no source gives the number and the project chose it; the skill (`references/infortuni.md`) says
why. Nothing here is medical advice.
"""

from __future__ import annotations

from typing import Final

from ponos_coach.injury.domain.vocabulary import BodyZone, Onset, Step

# --- Pain scale and the rules of the path (CSM1 cap. 4 and 36, SIL) ----------------------------
STEP_UP_MAX_PAIN_DURING: Final = 3  # up to 3/10 during the activity (SIL, CSM1)
TOLERATED_MAX_PAIN_DURING: Final = 5  # up to 5/10 only for a moment: hold the step (CSM1)
STRONG_REST_PAIN: Final = 5  # rest pain from this value is a warning sign (project default)

# The first running step needs 5 consecutive days with no pain in daily life (CSM1 cap. 4); a
# gradual heel pain needs 4 weeks (CSM1 cap. 43; the 10 km of pain-free walking it also asks for is
# not tracked: project default).
PAIN_FREE_DAYS_BEFORE_RUNNING: Final = 5
PAIN_FREE_DAYS_BEFORE_RUNNING_HEEL: Final = 28

# Consecutive days of check-ins, all good, to leave a step; and how many of them had an activity.
# Frequency first, then duration, then intensity (FER25): the first steps are shorter. CSM1 gives
# no numbers (project default). Sessions on alternate days in the first running steps (CSM1).
GOOD_DAYS_TO_STEP_UP: Final[dict[Step, int]] = {
    Step.ACTIVATION: 3,
    Step.WALKING: 3,
    Step.RUN_WALK: 4,
    Step.CONTINUOUS_EASY: 4,
    Step.VOLUME: 6,
    Step.INTENSITY: 6,
}
ACTIVE_DAYS_TO_STEP_UP: Final[dict[Step, int]] = {
    Step.ACTIVATION: 2,
    Step.WALKING: 2,
    Step.RUN_WALK: 2,
    Step.CONTINUOUS_EASY: 2,
    Step.VOLUME: 3,
    Step.INTENSITY: 3,
}
ALTERNATE_DAY_STEPS: Final = frozenset({Step.RUN_WALK, Step.CONTINUOUS_EASY})

# After a medical review of a warning sign the path starts again from the first step (project
# default: the safest; the doctor's notes are free text, the app does not read them).
STEP_AFTER_REVIEW: Final = Step.ACTIVATION

# --- Recovery slower than expected (CSM1 cap. 4, project default for the numbers) --------------
STEP_EXPECTED_DAYS: Final = 21  # three weeks on the same step: see a doctor instead of going on
TOTAL_EXPECTED_DAYS: Final = 84  # twelve weeks from the opening

# --- Limits of the data ------------------------------------------------------------------------
MAX_CHECKINS: Final = 400
MAX_NOTES_CHARS: Final = 2000
FIRST_DAY_YEAR: Final = 1900

# --- Where the load goes: which activities a zone allows, and from which step ------------------
# Cycling is no free alternative for the knee and the back: it is a step of the path, so from the
# walking step on (CSM1 cap. 9). The shin, foot and Achilles take it from the start (CSM1). The
# ankle and the hip are not covered by the source: project default (the ankle free, the hip
# prudent).
CYCLING_FROM_STEP: Final[dict[BodyZone, Step]] = {
    BodyZone.FOOT: Step.ACTIVATION,
    BodyZone.ANKLE: Step.ACTIVATION,
    BodyZone.ACHILLES: Step.ACTIVATION,
    BodyZone.CALF: Step.ACTIVATION,
    BodyZone.SHIN: Step.ACTIVATION,
    BodyZone.KNEE: Step.WALKING,
    BodyZone.THIGH: Step.ACTIVATION,
    BodyZone.HIP: Step.WALKING,
    BodyZone.LOWER_BACK: Step.WALKING,
    BodyZone.UPPER_BODY: Step.ACTIVATION,
}
WALKING_FROM_STEP: Final = Step.WALKING  # before that the movement is the activation step
# Strength sessions load the lower body: back from the first running step; the upper body never
# stops them (project default).
STRENGTH_FROM_STEP: Final[dict[BodyZone, Step]] = {
    zone: Step.RUN_WALK for zone in BodyZone if zone is not BodyZone.UPPER_BODY
} | {BodyZone.UPPER_BODY: Step.ACTIVATION}

# --- Illness (CSM2) ------------------------------------------------------------------------------
# Above the neck and no fever: only the shortest easy session, no intensity. With fever, malaise or
# aching muscles: no training until the user says they feel well.

# --- After the return (CSM1 cap. 12, project default for the window) ---------------------------
RAMP_AFTER_RETURN_DAYS: Final = 28  # the lower weekly ramp lasts this long after closing

# --- Who is heel pain --------------------------------------------------------------------------
HEEL_ZONE: Final = BodyZone.FOOT
HEEL_ONSET: Final = Onset.GRADUAL
