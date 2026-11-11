# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Time zone validation lives outside the pure domain: its cases are here."""

from __future__ import annotations

import pytest

from ponos_coach.profiles.timezones import require_known_timezone


@pytest.mark.parametrize("zone", ["Europe/Rome", "UTC", "America/Argentina/Buenos_Aires"])
def test_known_zones_are_accepted(zone: str) -> None:
    require_known_timezone(zone)


@pytest.mark.parametrize(
    "zone", ["", "Mars/Olympus", "../etc/localtime", "x" * 65, "Europe/Rome\x00", "/etc/passwd"]
)
def test_unknown_or_hostile_zones_are_refused(zone: str) -> None:
    with pytest.raises(ValueError, match="time zone"):
        require_known_timezone(zone)
