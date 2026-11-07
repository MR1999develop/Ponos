# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Physiological parameters with validity date and fingerprint of the load parameters."""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain.heart_rate import Physiology
from ponos_coach.load.domain.sport import SportWeight
from ponos_coach.load.domain.zones import ZONE_STARTS_PERCENT

# Changes when the load formula or the zone definition changes: invalidates every saved
# load. 2: saved loads also carry the seconds in zone.
LOAD_MODEL_VERSION = 2
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class PhysiologyEntry:
    """Physiological parameters valid from a day (included) until the next entry."""

    valid_from: date
    physiology: Physiology


@dataclass(frozen=True, slots=True)
class LoadFingerprint:
    """Fingerprint (lowercase hexadecimal SHA-256) of the parameters that produced a load."""

    hex: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.hex):
            raise ValueError("the fingerprint must be 64 lowercase hexadecimal digits")


def load_fingerprint(
    physiology: Physiology, sport_weights: Mapping[Sport, SportWeight]
) -> LoadFingerprint:
    """Fingerprint of the parameters that determine the load of an activity.

    It depends on the HR limits, the threshold, the sex, the sport weights set
    (insertion order does not matter), the zone percentages and the version of the load
    model. Whoever computes the load of a sport passes only the weight of that sport, so
    changing the bike weight does not invalidate the runs. Deterministic:
    the same parameters give the same fingerprint, in every process.
    """
    document = {
        "model": LOAD_MODEL_VERSION,
        "min_bpm": physiology.limits.min_bpm,
        "max_bpm": physiology.limits.max_bpm,
        "threshold_bpm": physiology.threshold.bpm,
        "sex": physiology.sex.value,
        "zones": list(ZONE_STARTS_PERCENT),
        "weights": {sport.value: weight.value for sport, weight in sport_weights.items()},
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return LoadFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest())


class MissingPhysiologyError(ValueError):
    """There is no physiological parameter: without it, the load cannot be computed."""


def physiology_for_day(entries: Sequence[PhysiologyEntry], day: date) -> Physiology:
    """Parameters valid on a day: those with the most recent validity date not after it.

    `entries` is sorted by ascending validity date, one entry per date (as given by
    `PhysiologyStore.list_entries`). Project default: for days before the first
    parameter the first one applies, so a history prior to the first setting is computed with
    the oldest known parameters instead of being left without load.

    Raises:
        MissingPhysiologyError: if `entries` is empty.
    """
    if not entries:
        raise MissingPhysiologyError("no physiological parameter set")
    index = bisect_right([entry.valid_from for entry in entries], day) - 1
    return entries[max(index, 0)].physiology
