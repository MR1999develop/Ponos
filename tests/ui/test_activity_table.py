# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity table: rows, alignment, empty cells, sorting and empty state."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QLocale, Qt
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import LoadSource
from ponos_coach.load.use_cases import ProfileActivity
from ponos_coach.ui.activity_table import ActivitiesPage, ActivityTableModel, Column
from tests.ui.helpers import profile_activity

ROME = ZoneInfo("Europe/Rome")


def activities() -> list[ProfileActivity]:
    return [
        profile_activity(datetime(2026, 3, 2, 6, 30, tzinfo=UTC), load=50.0, serial=1),
        profile_activity(
            datetime(2026, 3, 4, 17, 0, tzinfo=UTC),
            sport=Sport.CYCLING,
            distance_m=None,
            ascent_m=None,
            load=120.0,
            source=LoadSource.ESTIMATE,
            serial=2,
            avg_bpm=None,
            max_bpm=None,
        ),
        profile_activity(
            datetime(2026, 3, 3, 12, 0, tzinfo=UTC),
            distance_m=21_097.5,
            load=90.0,
            serial=3,
            avg_bpm=99,
            max_bpm=150,
        ),
    ]


def cell(page: ActivitiesPage, row: int, column: Column) -> str:
    return str(page.proxy.index(row, column).data())


def test_the_model_shows_local_time_units_and_empty_cells(qtbot: QtBot) -> None:
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    model = ActivityTableModel()
    model.set_activities(activities(), ROME)

    def text(row: int, column: Column) -> str:
        return str(model.index(row, column).data())

    assert "7:30" in text(0, Column.START)  # 06:30 UTC = 07:30 in Rome (CET)
    assert text(0, Column.SPORT) == "Running"
    assert text(0, Column.MOVING_TIME) == "1:00:00"
    assert text(0, Column.DISTANCE) == "10.00"
    assert text(0, Column.ASCENT) == "120"
    assert text(0, Column.LOAD) == "50"
    assert text(0, Column.LOAD_SOURCE) == "Heart rate"
    assert text(0, Column.TOTAL_TIME) == "1:01:40"  # elapsed 3700 s
    assert text(0, Column.AVG_HR) == "148"
    assert text(0, Column.MAX_HR) == "176"
    assert text(1, Column.AVG_HR) == ""  # no HR: empty cell, like the other missing data
    assert text(1, Column.MAX_HR) == ""
    assert text(1, Column.DISTANCE) == ""  # sessions without distance: empty cell
    assert text(1, Column.ASCENT) == ""
    assert text(1, Column.LOAD_SOURCE) == "Estimated"


def test_headers_carry_the_units_and_numbers_are_right_aligned() -> None:
    model = ActivityTableModel()
    model.set_activities(activities(), ROME)
    horizontal = Qt.Orientation.Horizontal

    assert model.headerData(Column.DISTANCE, horizontal) == "Distance (km)"
    assert model.headerData(Column.ASCENT, horizontal) == "Ascent (m)"
    assert model.headerData(Column.MOVING_TIME, horizontal) == "Moving time (h:mm:ss)"
    assert model.headerData(Column.TOTAL_TIME, horizontal) == "Total time (h:mm:ss)"
    assert model.headerData(Column.AVG_HR, horizontal) == "Avg HR (bpm)"
    assert model.headerData(Column.MAX_HR, horizontal) == "Max HR (bpm)"
    right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    for column in (
        Column.MOVING_TIME,
        Column.TOTAL_TIME,
        Column.DISTANCE,
        Column.ASCENT,
        Column.LOAD,
        Column.AVG_HR,
        Column.MAX_HR,
    ):
        align = model.index(0, column).data(Qt.ItemDataRole.TextAlignmentRole)
        assert align == right
    assert model.index(0, Column.SPORT).data(Qt.ItemDataRole.TextAlignmentRole) != right
    assert model.rowCount() == 3
    assert model.columnCount() == len(Column)


def test_the_page_sorts_by_date_descending_and_by_any_column(qtbot: QtBot) -> None:
    page = ActivitiesPage()
    qtbot.addWidget(page)
    page.show_activities(activities(), ROME)

    def loads() -> list[str]:
        return [cell(page, row, Column.LOAD) for row in range(3)]

    assert loads() == ["120", "90", "50"]  # most recent first
    page.proxy.sort(Column.LOAD, Qt.SortOrder.AscendingOrder)
    assert loads() == ["50", "90", "120"]
    page.proxy.sort(Column.DISTANCE, Qt.SortOrder.DescendingOrder)
    assert loads() == ["90", "50", "120"]  # the numbers, not the text; the empty cell at the bottom


def test_columns_come_in_the_requested_order_and_there_is_no_format_column() -> None:
    model = ActivityTableModel()
    horizontal = Qt.Orientation.Horizontal
    headers = [str(model.headerData(c, horizontal)) for c in range(model.columnCount())]

    assert [c.name for c in Column] == [
        "START", "SPORT", "MOVING_TIME", "TOTAL_TIME", "DISTANCE", "ASCENT", "LOAD",
        "AVG_HR", "MAX_HR", "LOAD_SOURCE",
    ]  # fmt: skip
    assert "Format" not in headers
    assert len(headers) == 10


def test_heart_rate_columns_sort_by_number_with_missing_values_last(qtbot: QtBot) -> None:
    page = ActivitiesPage()
    qtbot.addWidget(page)
    page.show_activities(activities(), ROME)

    page.proxy.sort(Column.AVG_HR, Qt.SortOrder.DescendingOrder)
    assert [cell(page, row, Column.AVG_HR) for row in range(3)] == ["148", "99", ""]
    page.proxy.sort(Column.MAX_HR, Qt.SortOrder.DescendingOrder)
    assert cell(page, 2, Column.MAX_HR) == ""


def test_the_page_shows_the_empty_state_until_there_are_activities(qtbot: QtBot) -> None:
    page = ActivitiesPage()
    qtbot.addWidget(page)
    page.show()
    assert page.empty_page.isVisible()
    assert not page.table.isVisible()
    requests: list[bool] = []
    page.import_requested.connect(lambda: requests.append(True))
    page.empty_page.button.click()
    assert requests == [True]

    page.show_activities(activities(), ROME)

    assert page.table.isVisible()
    assert not page.table.showGrid()
    page.show_activities([], ROME)
    assert page.empty_page.isVisible()
