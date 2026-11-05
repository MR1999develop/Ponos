# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Valori comuni ai test del carico: i dati degli esempi A-E di `modello-carico.md`."""

from __future__ import annotations

from pathlib import Path

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    SportWeight,
    ThresholdHeartRate,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "data"

# FCmin 50, FCmax 190, soglia 170, uomo (k = 1,92).
PHYSIOLOGY = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
WEIGHT_ONE = SportWeight(1.0)
