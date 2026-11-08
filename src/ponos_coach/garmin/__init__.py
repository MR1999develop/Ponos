# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin adapter (garminconnect): `GarminAccount`, `ActivitySource` and `WorkoutCalendar` ports.

Composition Root: `GarminConnectAccount(ConnectClientFactory())`.
"""

from ponos_coach.garmin.account import GarminConnectAccount, GarminConnectSession
from ponos_coach.garmin.client import GarminClient, GarminClientFactory
from ponos_coach.garmin.connect import ConnectClientFactory
from ponos_coach.garmin.source import GarminActivitySource
from ponos_coach.garmin.workout_client import GarminWorkoutClient
from ponos_coach.garmin.workouts import GarminWorkoutCalendar

__all__ = [
    "ConnectClientFactory",
    "GarminActivitySource",
    "GarminClient",
    "GarminClientFactory",
    "GarminConnectAccount",
    "GarminConnectSession",
    "GarminWorkoutCalendar",
    "GarminWorkoutClient",
]
