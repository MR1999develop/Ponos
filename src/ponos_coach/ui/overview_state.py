# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Stato di presentazione della panoramica: testabile senza finestra.

Gli stati sono un'unione di dataclass congelate; `page_of` decide quale pagina mostrare,
`primary_action_of` quale comando offre il bottone della pagina e `summary_of` i numeri della
barra di stato. La vista non decide nulla.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.ui.errors import ErrorMessage


@dataclass(frozen=True, slots=True)
class Idle:
    """Nessun profilo aperto."""


@dataclass(frozen=True, slots=True)
class Loading:
    """Profilo in apertura o calcolo in corso; `total` è 0 finché non è noto."""

    done: int
    total: int


@dataclass(frozen=True, slots=True)
class Ready:
    """Calcolo concluso."""

    overview: ProfileOverview


@dataclass(frozen=True, slots=True)
class Cancelled:
    """Calcolo annullato dall'utente."""


@dataclass(frozen=True, slots=True)
class Failed:
    """Calcolo fallito, con il messaggio per l'utente."""

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


class PrimaryAction(Enum):
    """Comando del bottone di una pagina di stato."""

    NEW_PROFILE = "new_profile"
    IMPORT_FILES = "import_files"
    RECALCULATE = "recalculate"


def page_of(state: OverviewState) -> Page:
    """Pagina da mostrare per lo stato."""
    match state:
        case Idle():
            return Page.START
        case Loading():
            return Page.LOADING
        case Ready(overview):
            return Page.CHART if len(overview.pmc) > 0 else Page.NO_ACTIVITIES
        case Cancelled():
            return Page.CANCELLED
        case Failed():
            return Page.ERROR


def primary_action_of(page: Page) -> PrimaryAction | None:
    """Comando offerto dal bottone della pagina (nessuno per grafico e avanzamento)."""
    match page:
        case Page.START:
            return PrimaryAction.NEW_PROFILE
        case Page.NO_ACTIVITIES:
            return PrimaryAction.IMPORT_FILES
        case Page.CANCELLED | Page.ERROR:
            return PrimaryAction.RECALCULATE
        case Page.LOADING | Page.CHART:
            return None


@dataclass(frozen=True, slots=True)
class OverviewSummary:
    """Numeri della barra di stato."""

    activities: int
    excluded: int
    pmc_days: int


def summary_of(state: OverviewState) -> OverviewSummary | None:
    """I numeri di un calcolo concluso; `None` negli altri stati."""
    if isinstance(state, Ready):
        overview = state.overview
        return OverviewSummary(len(overview.activities), len(overview.excluded), len(overview.pmc))
    return None
