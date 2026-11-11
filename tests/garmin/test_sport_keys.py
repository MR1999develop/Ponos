# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The sport keys `garminconnect` names (Garmin parents) and the domain map.

The library lists only the generic categories (`get_activities_by_date`: cycling, running,
swimming, multi_sport, fitness_equipment, hiking, walking, other) and `strength_training` in
its workouts; the subcategory keys come from the Garmin site and will have to be
confirmed with the real responses of the spike. These tests pin down that the domain handles them.
"""

from __future__ import annotations

import pytest

from ponos_coach.activities.domain import Sport, sport_from_garmin_key


@pytest.mark.parametrize(
    ("key", "sport"),
    [
        ("running", Sport.RUNNING),
        ("cycling", Sport.CYCLING),
        ("hiking", Sport.HIKING),
        ("walking", Sport.WALKING),
        ("strength_training", Sport.STRENGTH),
        ("swimming", Sport.OTHER),
        ("multi_sport", Sport.OTHER),
        ("fitness_equipment", Sport.OTHER),
        ("other", Sport.OTHER),
        ("una_chiave_mai_vista", Sport.OTHER),
    ],
)
def test_keys_named_by_the_library_have_a_sport(key: str, sport: Sport) -> None:
    assert sport_from_garmin_key(key) is sport


def test_the_domain_takes_keys_in_lower_case_only() -> None:
    assert sport_from_garmin_key("Running") is Sport.OTHER
