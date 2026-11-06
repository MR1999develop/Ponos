# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Impronta dei parametri del carico e voci fisiologiche con data di validità."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    LoadFingerprint,
    Physiology,
    Sex,
    SportWeight,
    ThresholdHeartRate,
    load_fingerprint,
)
from tests.builders import physiology, physiology_entry


def test_fingerprint_is_deterministic_and_a_sha256() -> None:
    first = load_fingerprint(physiology(), {})

    assert first == load_fingerprint(physiology(), {})
    assert len(first.hex) == 64


def test_weight_insertion_order_does_not_matter() -> None:
    weights = {Sport.RUNNING: SportWeight(1.0), Sport.CYCLING: SportWeight(0.8)}
    expected = load_fingerprint(physiology(), weights)

    assert load_fingerprint(physiology(), dict(reversed(weights.items()))) == expected


@pytest.mark.parametrize(
    "changed",
    [
        Physiology(HeartRateLimits(51, 190), ThresholdHeartRate(170), Sex.MALE),
        Physiology(HeartRateLimits(50, 191), ThresholdHeartRate(170), Sex.MALE),
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(171), Sex.MALE),
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.FEMALE),
    ],
)
def test_each_physiological_parameter_changes_the_fingerprint(changed: Physiology) -> None:
    assert load_fingerprint(changed, {}) != load_fingerprint(physiology(), {})


def test_sport_weights_change_the_fingerprint() -> None:
    base = load_fingerprint(physiology(), {})
    one = load_fingerprint(physiology(), {Sport.RUNNING: SportWeight(1.0)})
    other = load_fingerprint(physiology(), {Sport.RUNNING: SportWeight(1.1)})
    cycling = load_fingerprint(physiology(), {Sport.CYCLING: SportWeight(1.0)})

    assert len({base, one, other, cycling}) == 4


def test_fingerprint_text_must_be_lowercase_hex_of_64_digits() -> None:
    LoadFingerprint("0" * 64)
    for text in ("", "A" * 64, "0" * 63, "0" * 65, "z" * 64):
        with pytest.raises(ValueError, match="impronta"):
            LoadFingerprint(text)


def test_entry_keeps_its_date_and_parameters() -> None:
    entry = physiology_entry(date(2026, 1, 1), 168)

    assert entry.valid_from == date(2026, 1, 1)
    assert entry.physiology.threshold.bpm == 168
