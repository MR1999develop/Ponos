# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Garmin adapter (garminconnect): `GarminAccount` and `ActivitySource` ports.

Composition Root: `GarminConnectAccount(ConnectClientFactory())`.
"""

from ponos_coach.garmin.account import GarminConnectAccount
from ponos_coach.garmin.client import GarminClient, GarminClientFactory
from ponos_coach.garmin.connect import ConnectClientFactory
from ponos_coach.garmin.source import GarminActivitySource

__all__ = [
    "ConnectClientFactory",
    "GarminActivitySource",
    "GarminClient",
    "GarminClientFactory",
    "GarminConnectAccount",
]
