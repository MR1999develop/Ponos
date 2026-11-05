# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Sport riconosciuti di un'attivita."""

from __future__ import annotations

from enum import Enum


class Sport(Enum):
    """Sport di un'attivita. Il valore e un identificatore stabile (usato nei dati salvati)."""

    RUNNING = "running"
    TRAIL = "trail"
    CYCLING = "cycling"
    STRENGTH = "strength"
    HIKING = "hiking"
    WALKING = "walking"
    OTHER = "other"
