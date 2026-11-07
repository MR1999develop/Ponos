# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Measurement history table: rows by date, alignment, sorting and empty cells."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import Weight, WeightEntry
from ponos_coach.profiles.measurements import MeasurementHistory
from ponos_coach.ui.measurement_history_model import (
    Column,
    MeasurementHistoryModel,
    history_rows,
    sorted_history_model,
)


def physiology(threshold: int) -> Physiology:
    return Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold), Sex.MALE)


HISTORY = MeasurementHistory(
    None,
    (
        WeightEntry(date(2026, 3, 1), Weight(690)),
        WeightEntry(date(2026, 5, 10), Weight(705)),
    ),
    (
        PhysiologyEntry(date(2026, 1, 1), physiology(171)),
        PhysiologyEntry(date(2026, 5, 10), physiology(165)),
    ),
)


def test_one_row_per_date_with_a_record_in_ascending_order() -> None:
    rows = history_rows(HISTORY)

    assert [row.day for row in rows] == [date(2026, 1, 1), date(2026, 3, 1), date(2026, 5, 10)]
    assert rows[0].weight is None
    assert rows[1].physiology is None
    assert rows[2].weight is not None
    assert rows[2].physiology is not None


def test_cells_are_empty_where_nothing_was_recorded_and_numbers_align_right(
    qtbot: QtBot,
) -> None:
    model = MeasurementHistoryModel()
    model.set_history(HISTORY)

    def text(row: int, column: Column) -> object:
        return model.data(model.index(row, column), Qt.ItemDataRole.DisplayRole)

    assert text(0, Column.WEIGHT) == ""
    assert text(0, Column.THRESHOLD) == "171"
    assert text(1, Column.THRESHOLD) == ""
    assert text(1, Column.WEIGHT) not in ("", None)
    right = model.data(model.index(2, Column.WEIGHT), Qt.ItemDataRole.TextAlignmentRole)
    left = model.data(model.index(2, Column.DATE), Qt.ItemDataRole.TextAlignmentRole)
    assert isinstance(right, int)
    assert right & int(Qt.AlignmentFlag.AlignRight)
    assert isinstance(left, int)
    assert left & int(Qt.AlignmentFlag.AlignLeft)


def test_headers_carry_the_units() -> None:
    model = MeasurementHistoryModel()

    headers = [
        model.headerData(column, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        for column in Column
    ]

    assert headers == [
        "Valid from",
        "Weight (kg)",
        "Resting HR (bpm)",
        "Maximum HR (bpm)",
        "Threshold HR (bpm)",
    ]


def test_the_view_orders_by_date_most_recent_first_and_by_any_column(qtbot: QtBot) -> None:
    model = MeasurementHistoryModel()
    model.set_history(HISTORY)
    proxy = sorted_history_model(model)

    def days() -> list[object]:
        return [
            proxy.data(proxy.index(row, Column.THRESHOLD), Qt.ItemDataRole.DisplayRole)
            for row in range(proxy.rowCount())
        ]

    assert days() == ["165", "", "171"]  # 10 May, 1 March, 1 January

    proxy.sort(Column.DATE, Qt.SortOrder.AscendingOrder)
    assert days() == ["171", "", "165"]
    proxy.sort(Column.THRESHOLD, Qt.SortOrder.AscendingOrder)
    assert days() == ["", "165", "171"]  # the empty cell comes first


def test_an_empty_history_has_no_rows_and_no_child_rows() -> None:
    model = MeasurementHistoryModel()
    model.set_history(MeasurementHistory(None, (), ()))

    assert model.rowCount() == 0
    assert model.columnCount() == len(Column)
    assert model.data(model.index(0, 0)) is None
