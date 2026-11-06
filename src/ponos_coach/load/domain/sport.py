# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Pesi di default degli sport e intensita tipiche per la stima senza FC."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from ponos_coach.activities.domain import Sport


@dataclass(frozen=True, slots=True)
class SportWeight:
    """Peso di uno sport nel carico (senza unita), tra 0 e `MAX`; 0 azzera il carico."""

    MAX = 5.0

    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not 0.0 <= self.value <= self.MAX:
            raise ValueError(f"peso dello sport fuori da [0, {self.MAX}]")


# Default del progetto (PLAN.md sezione 2 punto 7), provvisori, da tarare in fase 3.
_DEFAULT_WEIGHTS: dict[Sport, float] = {
    Sport.RUNNING: 1.0,
    Sport.TRAIL: 1.1,
    Sport.CYCLING: 0.8,
    Sport.HIKING: 0.8,
    Sport.WALKING: 0.6,
    Sport.STRENGTH: 1.2,
    Sport.OTHER: 0.8,
}

# Default del progetto: intensita tipica IF (frazione della soglia, senza unita) per la
# stima senza FC; hrTSS stimato = durata_h * 100 * IF^2. Da tarare in fase 3.
_TYPICAL_INTENSITY: dict[Sport, float] = {
    Sport.RUNNING: 0.80,
    Sport.TRAIL: 0.80,
    Sport.CYCLING: 0.65,
    Sport.HIKING: 0.55,
    Sport.WALKING: 0.45,
    Sport.STRENGTH: 0.50,
    Sport.OTHER: 0.55,
}


def default_weight(sport: Sport) -> SportWeight:
    """Peso di default dello sport."""
    return SportWeight(_DEFAULT_WEIGHTS[sport])


def effective_weight(sport: Sport, saved: Mapping[Sport, SportWeight]) -> SportWeight:
    """Il peso di uno sport: quello salvato dall'utente, altrimenti il default (unico punto)."""
    return saved.get(sport, default_weight(sport))


def effective_weights(saved: Mapping[Sport, SportWeight]) -> dict[Sport, SportWeight]:
    """Il peso di ogni sport: quello salvato dall'utente, altrimenti il default."""
    return {sport: effective_weight(sport, saved) for sport in Sport}


def customised_weights(weights: Mapping[Sport, SportWeight]) -> dict[Sport, SportWeight]:
    """Solo i pesi diversi dal default: ciò che vale la pena salvare (forma canonica)."""
    return {sport: w for sport, w in weights.items() if w != default_weight(sport)}


def typical_intensity(sport: Sport) -> float:
    """Intensita tipica (IF, tra 0 e 1) usata per stimare il carico senza FC."""
    return _TYPICAL_INTENSITY[sport]
