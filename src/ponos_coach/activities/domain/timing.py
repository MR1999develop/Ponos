# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controlli comuni su istanti e durate di un'attività."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def check_utc(moment: datetime) -> None:
    """Rifiuta un istante senza fuso o con un fuso diverso da UTC."""
    if moment.utcoffset() != timedelta(0):
        raise ValueError("gli istanti devono essere UTC con fuso")


def truncate_to_millisecond(moment: datetime) -> datetime:
    """L'istante senza la frazione sotto il millisecondo (per difetto, fuso conservato).

    L'indice delle attività salva i millisecondi interi e rifiuta frazioni più fini: i
    lettori e il caso d'uso dell'import le tolgono qui, in un solo posto.
    """
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def check_durations(elapsed_s: float, timer_s: float) -> None:
    """Durate finite e positive, con il tempo in movimento non oltre il trascorso."""
    for duration in (elapsed_s, timer_s):
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("le durate devono essere finite e positive")
    if timer_s > elapsed_s:
        raise ValueError("il tempo in movimento non può superare il trascorso")
