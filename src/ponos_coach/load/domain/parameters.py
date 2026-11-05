# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Parametri fisiologici con data di validità e impronta dei parametri del carico."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain.heart_rate import Physiology
from ponos_coach.load.domain.sport import SportWeight

# Cambia quando cambia la formula del carico: invalida ogni carico salvato.
LOAD_MODEL_VERSION = 1
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class PhysiologyEntry:
    """Parametri fisiologici validi da un giorno (incluso) fino alla voce successiva."""

    valid_from: date
    physiology: Physiology


@dataclass(frozen=True, slots=True)
class LoadFingerprint:
    """Impronta (SHA-256 esadecimale minuscolo) dei parametri che hanno prodotto un carico."""

    hex: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.hex):
            raise ValueError("l'impronta deve essere di 64 cifre esadecimali minuscole")


def load_fingerprint(
    physiology: Physiology, sport_weights: Mapping[Sport, SportWeight]
) -> LoadFingerprint:
    """Impronta dei parametri che determinano il carico di un'attività.

    Dipende dai limiti di FC, dalla soglia, dal sesso, dai pesi degli sport impostati
    (l'ordine di inserimento non conta) e dalla versione del modello di carico. Deterministica:
    gli stessi parametri danno la stessa impronta, in ogni processo.
    """
    document = {
        "model": LOAD_MODEL_VERSION,
        "min_bpm": physiology.limits.min_bpm,
        "max_bpm": physiology.limits.max_bpm,
        "threshold_bpm": physiology.threshold.bpm,
        "sex": physiology.sex.value,
        "weights": {sport.value: weight.value for sport, weight in sport_weights.items()},
    }
    text = json.dumps(document, sort_keys=True, separators=(",", ":"))
    return LoadFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest())
