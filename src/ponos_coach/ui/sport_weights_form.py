# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modulo "Pesi degli sport": valori e regole, testabili senza finestra.

Un peso va da 0 a `SportWeight.MAX` con due decimali; fuori intervallo si riporta al limite.
"Ripristina predefiniti" rimette i pesi di default del dominio. Il modulo non conosce widget né
testi.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight, default_weight

WEIGHT_DECIMALS = 2
WEIGHT_STEP = 0.05


def _clamped(value: float) -> float:
    return round(min(max(value, 0.0), SportWeight.MAX), WEIGHT_DECIMALS)


class SportWeightsForm:
    """Un peso per ogni sport, partendo da quelli in vigore."""

    def __init__(self, current: Mapping[Sport, SportWeight]) -> None:
        self._initial = {sport: _clamped(current[sport].value) for sport in Sport}
        self._values = dict(self._initial)

    def value(self, sport: Sport) -> float:
        """Il peso mostrato per lo sport."""
        return self._values[sport]

    def set_value(self, sport: Sport, value: float) -> None:
        """L'utente cambia un peso; fuori intervallo si porta al limite più vicino. Un valore non
        numerico (NaN) non cambia nulla: azzerare il peso toglierebbe lo sport dal carico."""
        if not math.isnan(value):
            self._values[sport] = _clamped(value)

    def restore_defaults(self) -> None:
        """Rimette i pesi di default di tutti gli sport."""
        self._values = {sport: _clamped(default_weight(sport).value) for sport in Sport}

    @property
    def is_default(self) -> bool:
        """Vero se tutti i pesi sono quelli di default."""
        return all(self._values[sport] == default_weight(sport).value for sport in Sport)

    @property
    def has_changes(self) -> bool:
        """Vero se almeno un peso è diverso da quello in vigore all'apertura."""
        return self._values != self._initial

    def build(self) -> dict[Sport, SportWeight]:
        """I pesi da salvare: uno per sport (il caso d'uso tiene solo i diversi dal default)."""
        return {sport: SportWeight(value) for sport, value in self._values.items()}
