# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Values common to the load tests: the data of examples A-E of `modello-carico.md`."""

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

# FCmin 50, FCmax 190, threshold 170, male (k = 1.92).
PHYSIOLOGY = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
WEIGHT_ONE = SportWeight(1.0)
