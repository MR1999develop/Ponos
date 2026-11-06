# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Regola dei possibili duplicati tra formati: stesso inizio e stessa durata (PLAN.md 4.3).

Una sola definizione: indici, fake e casi d'uso la usano; un adattatore può prefiltrare con
un intervallo più largo ma poi applica questa funzione.
"""

from __future__ import annotations

from datetime import datetime, timedelta

DUPLICATE_START_TOLERANCE = timedelta(seconds=60)  # default del progetto
DUPLICATE_DURATION_TOLERANCE_PERCENT = 5  # default del progetto
_MS_PER_S = 1000
_PERCENT = 100


def is_possible_duplicate(
    start_a: datetime, elapsed_a_s: float, start_b: datetime, elapsed_b_s: float
) -> bool:
    """Vero se due attività potrebbero essere la stessa registrata in due formati.

    Gli inizi distano al più 60 s e i tempi trascorsi differiscono al più del 5% del maggiore
    (confronto in millisecondi interi, estremi inclusi). Simmetrica: l'ordine di arrivo non
    conta.
    """
    if abs(start_a - start_b) > DUPLICATE_START_TOLERANCE:
        return False
    ms_a, ms_b = round(elapsed_a_s * _MS_PER_S), round(elapsed_b_s * _MS_PER_S)
    return abs(ms_a - ms_b) * _PERCENT <= DUPLICATE_DURATION_TOLERANCE_PERCENT * max(ms_a, ms_b)
