# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modello di presentazione: quale pagina e quali numeri, senza finestra."""

from __future__ import annotations

from pathlib import Path

from ponos_coach.load.domain import PmcSeries
from ponos_coach.load.use_cases import FitnessOverview, RejectedFile, RejectionReason
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.overview_state import (
    Cancelled,
    Failed,
    Idle,
    Loading,
    OverviewSummary,
    Page,
    Ready,
    page_of,
    summary_of,
)
from tests.ui.helpers import FIRST_DAY, small_history

FOLDER = Path("fit")


def test_each_state_maps_to_its_page() -> None:
    pmc, entries = small_history()
    ready = Ready(FOLDER, FitnessOverview(pmc, tuple(entries), ()))
    empty = Ready(FOLDER, FitnessOverview(PmcSeries.empty(FIRST_DAY), (), ()))

    assert page_of(Idle()) is Page.START
    assert page_of(Loading(FOLDER, 1, 3)) is Page.LOADING
    assert page_of(ready) is Page.CHART
    assert page_of(empty) is Page.NO_ACTIVITIES
    assert page_of(Cancelled(FOLDER)) is Page.CANCELLED
    assert page_of(Failed(FOLDER, ErrorMessage("t", "w", "r"))) is Page.ERROR


def test_summary_counts_activities_rejected_files_and_pmc_days() -> None:
    pmc, entries = small_history()
    rejected = (RejectedFile("bad.fit", RejectionReason.CORRUPT),)
    ready = Ready(FOLDER, FitnessOverview(pmc, tuple(entries), rejected))

    assert summary_of(ready) == OverviewSummary(activities=3, rejected=1, pmc_days=10)
    assert summary_of(Idle()) is None
    assert summary_of(Loading(FOLDER, 0, 0)) is None
