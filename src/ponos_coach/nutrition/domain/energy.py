# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Energy: basal metabolic rate and the energy of an activity (declared, from HR, from METs).

Net energy of an activity is its total energy minus the basal metabolism of its duration, so the
day's expenditure (basal metabolism times a level factor, plus net activities) never counts the
resting part twice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np
from numpy.typing import NDArray

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import Sex
from ponos_coach.nutrition.domain import defaults
from ponos_coach.nutrition.domain.settings import ActivityLevel


@dataclass(frozen=True, slots=True)
class Person:
    """Who the advice is for, as of one day. Health data: never in logs or messages.

    `activity_level` is None when the user has not set one (the model then assumes the most
    conservative and says so).
    """

    sex: Sex
    age_years: int
    height_cm: int
    weight_kg: float
    activity_level: ActivityLevel | None = None

    def __post_init__(self) -> None:
        if not defaults.MIN_AGE_YEARS <= self.age_years <= defaults.MAX_AGE_YEARS:
            raise ValueError("age outside the plausible range")
        if not defaults.MIN_HEIGHT_CM <= self.height_cm <= defaults.MAX_HEIGHT_CM:
            raise ValueError("height outside the plausible range")
        if not (
            math.isfinite(self.weight_kg)
            and defaults.MIN_WEIGHT_KG <= self.weight_kg <= defaults.MAX_WEIGHT_KG
        ):
            raise ValueError("weight outside the plausible range")

    @property
    def body_mass_index(self) -> float:
        """Weight in kg divided by the square of the height in meters."""
        meters = self.height_cm / 100.0
        return self.weight_kg / (meters * meters)


def basal_metabolic_rate(person: Person) -> float:
    """Resting energy expenditure in kcal per day (Mifflin-St Jeor)."""
    offset = defaults.MSJ_MALE_OFFSET if person.sex is Sex.MALE else defaults.MSJ_FEMALE_OFFSET
    return (
        defaults.MSJ_PER_KG * person.weight_kg
        + defaults.MSJ_PER_CM * person.height_cm
        - defaults.MSJ_PER_YEAR * person.age_years
        + offset
    )


class EnergySource(Enum):
    """Where an activity's energy comes from."""

    DECLARED = "declared"  # the device's total, read from the file
    HEART_RATE = "heart_rate"  # estimated from the average heart rate (Keytel)
    MET = "met"  # estimated from the sport's METs


@dataclass(frozen=True, slots=True)
class ActivityEnergy:
    """Energy of an activity: total, net of the basal metabolism of its duration, and source."""

    total_kcal: float
    net_kcal: float
    source: EnergySource


def keytel_kcal_per_minute(person: Person, average_bpm: float) -> float:
    """Gross energy per minute at an average heart rate (Keytel et al. 2005), never negative."""
    constant, per_bpm, per_kg, per_year = (
        defaults.KEYTEL_MALE if person.sex is Sex.MALE else defaults.KEYTEL_FEMALE
    )
    kilojoules = (
        constant + per_bpm * average_bpm + per_kg * person.weight_kg + per_year * person.age_years
    )
    return max(0.0, kilojoules / defaults.KJ_PER_KCAL)


def met_kcal(sport: Sport, weight_kg: float, duration_s: float) -> float:
    """Gross energy of a sport at its typical METs: METs x kg x hours."""
    return defaults.SPORT_MET[sport] * weight_kg * duration_s / 3600.0


def average_valid_bpm(bpm: NDArray[np.float64], min_bpm: int, max_bpm: int) -> float | None:
    """Mean of the samples inside `[min_bpm, max_bpm]`; None if there are none."""
    valid = bpm[(bpm >= min_bpm) & (bpm <= max_bpm)]
    return float(valid.mean()) if valid.size else None


def activity_energy(
    person: Person,
    sport: Sport,
    duration_s: float,
    declared_kcal: float | None,
    average_bpm: float | None,
) -> ActivityEnergy:
    """Energy of an activity.

    The device's total when declared; otherwise Keytel's estimate if there is a usable average
    HR; otherwise the sport's METs. `duration_s` is the moving time. Net energy is the total minus
    the basal metabolism of that time, never below zero. Pure and deterministic.

    Raises:
        ValueError: `duration_s` is not finite and positive, or `declared_kcal` is not positive.
    """
    if not math.isfinite(duration_s) or duration_s <= 0:
        raise ValueError("the duration must be finite and positive")
    if declared_kcal is not None:
        if not math.isfinite(declared_kcal) or declared_kcal <= 0:
            raise ValueError("declared calories must be finite and positive")
        total, source = declared_kcal, EnergySource.DECLARED
    elif (
        average_bpm is not None
        and defaults.MIN_USABLE_BPM <= average_bpm <= defaults.MAX_USABLE_BPM
    ):
        total = keytel_kcal_per_minute(person, average_bpm) * duration_s / 60.0
        source = EnergySource.HEART_RATE
    else:
        total, source = met_kcal(sport, person.weight_kg, duration_s), EnergySource.MET
    resting = basal_metabolic_rate(person) * duration_s / defaults.SECONDS_PER_DAY
    return ActivityEnergy(total, max(0.0, total - resting), source)
