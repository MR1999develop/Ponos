# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Recognized sports of an activity."""

from __future__ import annotations

from enum import Enum


class Sport(Enum):
    """Sport of an activity. The value is a stable identifier (used in saved data)."""

    RUNNING = "running"
    TRAIL = "trail"
    CYCLING = "cycling"
    STRENGTH = "strength"
    HIKING = "hiking"
    WALKING = "walking"
    OTHER = "other"
