# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Regole dei tre valori di FC (minima, massima, soglia) che i moduli controllano sul posto.

Una sola fonte per "Nuovo profilo" e "Modifica profilo": il dominio le impone comunque (un test
verifica che gli intervalli coincidano), qui si dice solo quale regola è violata, in categorie.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QCoreApplication

# Intervalli dei controlli; il dominio li impone (un test verifica che coincidano).
MIN_ALLOWED_BPM = 20
MAX_ALLOWED_BPM = 250
MIN_THRESHOLD_BPM = 40


class HeartRateProblem(Enum):
    """Quale regola sui tre valori di FC è violata."""

    MIN_NOT_BELOW_MAX = "min_not_below_max"
    THRESHOLD_NOT_ABOVE_MIN = "threshold_not_above_min"
    THRESHOLD_ABOVE_MAX = "threshold_above_max"


def heart_rate_problems(
    min_bpm: int, max_bpm: int, threshold_bpm: int
) -> tuple[HeartRateProblem, ...]:
    """Regole violate da FC minima, massima e soglia (vuoto se i tre valori sono coerenti)."""
    found: list[HeartRateProblem] = []
    if min_bpm >= max_bpm:
        found.append(HeartRateProblem.MIN_NOT_BELOW_MAX)
    if threshold_bpm <= min_bpm:
        found.append(HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN)
    if threshold_bpm > max_bpm:
        found.append(HeartRateProblem.THRESHOLD_ABOVE_MAX)
    return tuple(found)


def heart_rate_problem_text(problem: HeartRateProblem) -> str:
    """Messaggio sotto il campo: quale regola non torna e come rimediare."""
    texts = {
        HeartRateProblem.MIN_NOT_BELOW_MAX: QCoreApplication.translate(
            "HeartRate", "The resting heart rate must be lower than the maximum."
        ),
        HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN: QCoreApplication.translate(
            "HeartRate", "The threshold must be higher than the resting heart rate."
        ),
        HeartRateProblem.THRESHOLD_ABOVE_MAX: QCoreApplication.translate(
            "HeartRate", "The threshold cannot be higher than the maximum."
        ),
    }
    return texts[problem]
