# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Le chiavi di sport che `garminconnect` nomina (genitori di Garmin) e la mappa del dominio.

La libreria elenca solo le categorie generiche (`get_activities_by_date`: cycling, running,
swimming, multi_sport, fitness_equipment, hiking, walking, other) e `strength_training` nei
suoi allenamenti; le chiavi delle sottocategorie vengono dal sito di Garmin e andranno
confermate con le risposte reali dello spike. Questi test fissano che il dominio le gestisce.
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
