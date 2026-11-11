# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The page "Deleted activities" without a window: rows, empty state, selection and Restore."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QLocale, Qt
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityIdentity, ActivitySummary, DeletedActivity, Sport
from ponos_coach.ui.deleted_activities_page import DeletedActivitiesPage, deleted_rows, title_of
from tests.builders import DELETED_AT, T0, fit_identity, make_deleted, make_record

ROME = ZoneInfo("Europe/Rome")


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def three() -> list[DeletedActivity]:
    """Three tombstones, as the store lists them (the latest deletion first)."""
    return [
        make_deleted(
            make_record(
                fit_identity(3),
                start=T0 + timedelta(days=2),
                sport=Sport.CYCLING,
                elapsed_s=5400.0,
                summary=ActivitySummary(distance_m=32_450.0),
            ),
            at=DELETED_AT + timedelta(minutes=2),
        ),
        make_deleted(
            make_record(fit_identity(1), start=T0, elapsed_s=3725.0), at=DELETED_AT + timedelta(1)
        ),
        make_deleted(
            make_record(
                fit_identity(2),
                start=T0 + timedelta(days=1),
                sport=Sport.STRENGTH,
                summary=ActivitySummary(),  # no distance
            ),
            at=DELETED_AT,
        ),
    ]


def test_the_rows_show_local_start_sport_duration_and_distance_with_an_empty_cell_for_none() -> (
    None
):
    rows = deleted_rows(three(), ROME)

    # The local time is 08:00 (07:00 UTC, CET); the exact format depends on the platform's locale.
    assert all("8:00" in row[0] for row in rows)
    assert [row[0].split()[0].rstrip(",") for row in rows] == ["3/4/26", "3/2/26", "3/3/26"]
    assert [row[1:] for row in rows] == [
        ("Cycling", "1:30:00", "32.45"),
        ("Running", "1:02:05", "10.00"),
        ("Strength", "1:00:00", ""),
    ]


def test_a_title_for_the_messages_names_the_sport_and_the_start() -> None:
    title = title_of(three()[1], ROME)

    assert title.startswith("Running, 3/2/26")
    assert "8:00" in title


def test_without_deleted_activities_a_sentence_replaces_the_table(qtbot: QtBot) -> None:
    page = DeletedActivitiesPage()
    qtbot.addWidget(page)

    at_start = page.is_empty_state
    assert page.empty_text.startswith("No deleted activities.")
    assert not page.restore_button.isEnabled()
    page.show_deleted(three(), ROME)
    with_rows = page.is_empty_state
    page.show_deleted([], ROME)
    emptied = page.is_empty_state

    assert (at_start, with_rows, emptied) == (True, False, True)


def test_restore_is_enabled_by_a_selection_and_gives_the_identities_in_list_order(
    qtbot: QtBot,
) -> None:
    page = DeletedActivitiesPage()
    qtbot.addWidget(page)
    page.show_deleted(three(), ROME)
    asked: list[tuple[ActivityIdentity, ...]] = []
    page.restore_requested.connect(asked.append)
    assert not page.restore_button.isEnabled()
    assert page.restore_button.toolTip() == "Select a deleted activity in the list first."

    page.table.selectionModel().select(
        page.table.model().index(2, 0),
        page.table.selectionModel().SelectionFlag.Select
        | page.table.selectionModel().SelectionFlag.Rows,
    )
    page.table.selectionModel().select(
        page.table.model().index(0, 0),
        page.table.selectionModel().SelectionFlag.Select
        | page.table.selectionModel().SelectionFlag.Rows,
    )
    assert page.restore_button.isEnabled()
    page.restore_button.click()

    assert asked == [(fit_identity(3), fit_identity(2))]


def test_restore_without_a_selection_does_nothing(qtbot: QtBot) -> None:
    page = DeletedActivitiesPage()
    qtbot.addWidget(page)
    page.show_deleted(three(), ROME)
    asked: list[object] = []
    page.restore_requested.connect(asked.append)

    page.request_restore()

    assert asked == []


def test_a_new_list_clears_the_selection_and_disables_restore(qtbot: QtBot) -> None:
    page = DeletedActivitiesPage()
    qtbot.addWidget(page)
    page.show_deleted(three(), ROME)
    page.table.selectRow(1)
    assert page.restore_button.isEnabled()

    page.show_deleted(three()[:1], ROME)

    assert not page.restore_button.isEnabled()
    assert page.selected_identities() == ()


def test_the_table_is_read_only_and_has_no_grid(qtbot: QtBot) -> None:
    page = DeletedActivitiesPage()
    qtbot.addWidget(page)
    page.show_deleted(three(), ROME)

    assert not page.table.showGrid()
    assert not page.table.model().flags(page.table.model().index(0, 0)) & Qt.ItemFlag.ItemIsEditable
