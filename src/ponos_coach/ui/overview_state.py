# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Stato di presentazione della panoramica: testabile senza finestra.

Gli stati sono un'unione di dataclass congelate; `page_of` decide quale pagina mostrare e
`summary_of` i numeri della barra di stato. La vista non decide nulla.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ponos_coach.load.use_cases import FitnessOverview
from ponos_coach.ui.errors import ErrorMessage


@dataclass(frozen=True, slots=True)
class Idle:
    """Nessuna cartella ancora scelta."""


@dataclass(frozen=True, slots=True)
class Loading:
    """Calcolo in corso; `total` e 0 finche l'elenco dei file non e noto."""

    folder: Path
    done: int
    total: int


@dataclass(frozen=True, slots=True)
class Ready:
    """Calcolo concluso."""

    folder: Path
    overview: FitnessOverview


@dataclass(frozen=True, slots=True)
class Cancelled:
    """Calcolo annullato dall'utente."""

    folder: Path


@dataclass(frozen=True, slots=True)
class Failed:
    """Calcolo fallito, con il messaggio per l'utente."""

    folder: Path
    message: ErrorMessage


type OverviewState = Idle | Loading | Ready | Cancelled | Failed


class Page(Enum):
    """Pagina della panoramica da mostrare."""

    START = "start"
    LOADING = "loading"
    NO_ACTIVITIES = "no_activities"
    CANCELLED = "cancelled"
    CHART = "chart"
    ERROR = "error"


def page_of(state: OverviewState) -> Page:
    """Pagina da mostrare per lo stato."""
    match state:
        case Idle():
            return Page.START
        case Loading():
            return Page.LOADING
        case Ready(_, overview):
            return Page.CHART if len(overview.pmc) > 0 else Page.NO_ACTIVITIES
        case Cancelled():
            return Page.CANCELLED
        case Failed():
            return Page.ERROR


@dataclass(frozen=True, slots=True)
class OverviewSummary:
    """Numeri della barra di stato."""

    activities: int
    rejected: int
    pmc_days: int


def summary_of(state: OverviewState) -> OverviewSummary | None:
    """I numeri di un calcolo concluso; `None` negli altri stati."""
    if isinstance(state, Ready):
        overview = state.overview
        return OverviewSummary(len(overview.activities), len(overview.rejected), len(overview.pmc))
    return None
