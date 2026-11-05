# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Attività e identità del file FIT da cui provengono."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from ponos_coach.load.domain import HeartRateSeries, Sport

_UINT16_MAX = 0xFFFF
_UINT32_MAX = 0xFFFFFFFF


@dataclass(frozen=True, slots=True)
class FileId:
    """Identità di un'attività: il `file_id` del FIT (PLAN.md sezione 4.3).

    Uguale per lo stesso file scaricato da Garmin o copiato dall'orologio. `time_created`
    è in UTC.
    """

    manufacturer: int
    product: int
    serial_number: int
    time_created: datetime

    def __post_init__(self) -> None:
        if not 0 <= self.manufacturer <= _UINT16_MAX or not 0 <= self.product <= _UINT16_MAX:
            raise ValueError("produttore o prodotto fuori da uint16")
        if not 0 <= self.serial_number <= _UINT32_MAX:
            raise ValueError("numero di serie fuori da uint32")
        if self.time_created.utcoffset() != timedelta(0):
            raise ValueError("time_created deve essere un istante UTC con fuso")


@dataclass(frozen=True, slots=True)
class Activity:
    """Un'attività: identità, sport, inizio UTC, durata e serie di FC (se c'è).

    `duration_s` è il tempo trascorso, finito e positivo. La serie ha i secondi dal suo
    primo campione.
    """

    file_id: FileId
    sport: Sport
    start: datetime
    duration_s: float
    heart_rate: HeartRateSeries | None

    def __post_init__(self) -> None:
        if self.start.utcoffset() != timedelta(0):
            raise ValueError("l'inizio deve essere un istante UTC con fuso")
        if not math.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("la durata deve essere finita e positiva")
