# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Modello di presentazione: quale pagina, quale comando e quali numeri, senza finestra."""

from __future__ import annotations

from datetime import UTC, datetime

from ponos_coach.load.domain import PmcSeries
from ponos_coach.load.use_cases import ExcludedActivity, ExclusionReason, ProfileOverview
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.overview_state import (
    Cancelled,
    Failed,
    Idle,
    Loading,
    OverviewSummary,
    Page,
    PrimaryAction,
    Ready,
    page_of,
    primary_action_of,
    summary_of,
)
from tests.ui.helpers import FIRST_DAY, profile_activity, small_history

START = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)


def test_each_state_maps_to_its_page() -> None:
    pmc, _ = small_history()
    ready = Ready(ProfileOverview(pmc, (profile_activity(START),), ()))
    empty = Ready(ProfileOverview(PmcSeries.empty(FIRST_DAY), (), ()))

    assert page_of(Idle()) is Page.START
    assert page_of(Loading(1, 3)) is Page.LOADING
    assert page_of(ready) is Page.CHART
    assert page_of(empty) is Page.NO_ACTIVITIES
    assert page_of(Cancelled()) is Page.CANCELLED
    assert page_of(Failed(ErrorMessage("t", "w", "r"))) is Page.ERROR


def test_each_page_offers_the_command_that_leads_to_the_next_step() -> None:
    assert primary_action_of(Page.START) is PrimaryAction.NEW_PROFILE
    assert primary_action_of(Page.NO_ACTIVITIES) is PrimaryAction.IMPORT_FILES
    assert primary_action_of(Page.CANCELLED) is PrimaryAction.RECALCULATE
    assert primary_action_of(Page.ERROR) is PrimaryAction.RECALCULATE
    assert primary_action_of(Page.LOADING) is None
    assert primary_action_of(Page.CHART) is None


def test_summary_counts_activities_excluded_and_pmc_days() -> None:
    pmc, _ = small_history()
    activity = profile_activity(START)
    excluded = (ExcludedActivity(activity.identity, ExclusionReason.FILE_MISSING),)
    ready = Ready(ProfileOverview(pmc, (activity,), excluded))

    assert summary_of(ready) == OverviewSummary(activities=1, excluded=1, pmc_days=10)
    assert summary_of(Idle()) is None
    assert summary_of(Loading(0, 0)) is None
