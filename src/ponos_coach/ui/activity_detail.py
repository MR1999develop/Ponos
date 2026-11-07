# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dettaglio di un'attività: esiti e righe delle zone, testabili senza finestra.

Il caso d'uso (`GetActivityDetail`) dà un `ActivityDetail` o il motivo per cui il file non si può
usare; qui l'esito diventa un valore per la vista, con il grafico già preparato fuori dal thread
della UI. I testi li sceglie la vista.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.load.activity_detail import ActivityDetail
from ponos_coach.load.domain import ZONE_COUNT
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.ui.chart_payload import PreparedChart
from ponos_coach.ui.load_figures import zone_label


@dataclass(frozen=True, slots=True)
class DetailReady:
    """Il dettaglio c'è. `chart` è `None` se l'attività non ha FC."""

    detail: ActivityDetail
    chart: PreparedChart | None


@dataclass(frozen=True, slots=True)
class DetailUnavailable:
    """Il file dell'attività non si può usare: il motivo è una categoria del dominio."""

    reason: ExclusionReason


type DetailOutcome = DetailReady | DetailUnavailable


@dataclass(frozen=True, slots=True)
class ZoneRow:
    """Tempo in una zona: nome con i confini, secondi e quota del tempo in zona (0-1)."""

    label: str
    seconds: float
    share: float


def zone_rows(detail: ActivityDetail) -> tuple[ZoneRow, ...]:
    """Una riga per zona, da Z1 a Z5. Senza tempo in zona (carico stimato) le quote sono 0."""
    seconds = detail.load.zone_seconds
    total = seconds.total
    return tuple(
        ZoneRow(
            zone_label(zone), seconds.values[zone], seconds.values[zone] / total if total else 0.0
        )
        for zone in range(ZONE_COUNT)
    )
