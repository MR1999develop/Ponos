# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation state of the overview: testable without a window.

The states are a union of frozen dataclasses; `page_of` decides which page to show,
`primary_action_of` which command the page button offers and `summary_of` the numbers of the
status bar. The view decides nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ponos_coach.load.use_cases import ProfileOverview
from ponos_coach.ui.errors import ErrorMessage


@dataclass(frozen=True, slots=True)
class Idle:
    """No profile open."""


@dataclass(frozen=True, slots=True)
class Loading:
    """Profile opening or computation in progress; `total` is 0 until known."""

    done: int
    total: int


@dataclass(frozen=True, slots=True)
class Ready:
    """Computation finished."""

    overview: ProfileOverview


@dataclass(frozen=True, slots=True)
class Cancelled:
    """Computation cancelled by the user."""


@dataclass(frozen=True, slots=True)
class Failed:
    """Computation failed, with the user message."""

    message: ErrorMessage


type OverviewState = Idle | Loading | Ready | Cancelled | Failed


class Page(Enum):
    """Overview page to show."""

    START = "start"
    LOADING = "loading"
    NO_ACTIVITIES = "no_activities"
    CANCELLED = "cancelled"
    CHART = "chart"
    ERROR = "error"


class PrimaryAction(Enum):
    """Command of a state page's button."""

    NEW_PROFILE = "new_profile"
    IMPORT_FILES = "import_files"
    RECALCULATE = "recalculate"


def page_of(state: OverviewState) -> Page:
    """Page to show for the state."""
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
    """Command offered by the page's button (none for chart and progress)."""
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
    """Status bar numbers."""

    activities: int
    excluded: int
    pmc_days: int


def summary_of(state: OverviewState) -> OverviewSummary | None:
    """The numbers of a finished computation; `None` in the other states."""
    if isinstance(state, Ready):
        overview = state.overview
        return OverviewSummary(len(overview.activities), len(overview.excluded), len(overview.pmc))
    return None
