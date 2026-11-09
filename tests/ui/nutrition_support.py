# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Builders and gestures shared by the calendar, day page and nutrition settings tests.

The profile and the activities are made up (no real person). Days are in June 2026 (the tests'
today is 30 June 2026), in the profile's time zone Europe/Rome (UTC+2 in summer).
"""

from __future__ import annotations

import re
import threading
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QLocale, QSettings, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ponos_coach.nutrition.domain import GoalPeriod
from ponos_coach.ui.calendar_model import CalendarDay
from ponos_coach.ui.day_page import DayPage, DayState
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_settings_dialog import NutritionSettingsDialog
from ponos_coach.ui.settings_page import SettingsCategory
from tests.fixtures.generators import fit_writer as fw
from tests.fixtures.generators.tracks import SEA_LAT_DEG, SEA_LON_DEG
from tests.ui.workspace_support import (
    WAIT_MS,
    compose,
    create_profile,
    wait_ready,
)

PROFILE = "Atleta di prova"
LOCAL_OFFSET_H = 2  # Europe/Rome in June
_STEP_S = 10
_NARROW_NO_BREAK_SPACE = chr(0x202F)


def plain(text: str) -> str:
    """The text with the narrow no-break space of the English time format as a normal space."""
    return text.replace(_NARROW_NO_BREAK_SPACE, " ")


def short_date(day: date) -> str:
    """A date as the dialogs show it (short format of the locale)."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def fit_at(
    day: date, local_hour: float, seconds: int, number: int, *, calories: int | None = None
) -> bytes:
    """FIT of a run starting at a local time of a day, `seconds` long, with heart rate."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    start = midnight + timedelta(hours=local_hour - LOCAL_OFFSET_H)
    first = fw.datetime_to_fit(start)
    samples = [
        fw.Sample(
            first + i * _STEP_S,
            135 + i % 15,
            lat_deg=SEA_LAT_DEG + i * 0.0001,
            lon_deg=SEA_LON_DEG,
            altitude_m=10.0,
        )
        for i in range(seconds // _STEP_S + 1)
    ]
    spec = fw.ActivitySpec(
        start,
        fw.Sport.RUNNING,
        fw.SubSport.GENERIC,
        samples,
        serial_number=3_000_000 + number,
        total_elapsed_ms=seconds * 1000,
        total_timer_ms=seconds * 1000,
        total_calories_kcal=calories,
    )
    return fw.build_activity(spec)


DAWN_AND_LUNCH_DAY = date(2026, 6, 10)  # a run at dawn and one at lunch
EMPTY_DAY = date(2026, 6, 11)
LONG_DAY = date(2026, 6, 12)  # a three hour session


def make_days_folder(tmp_path: Path) -> Path:
    """Folder with four activities: two on 10 June (06:00 and 12:30), one long on 12 June."""
    folder = tmp_path / "days"
    folder.mkdir()
    (folder / "dawn.fit").write_bytes(fit_at(DAWN_AND_LUNCH_DAY, 6.0, 1800, 1))
    (folder / "lunch.fit").write_bytes(fit_at(DAWN_AND_LUNCH_DAY, 12.5, 1800, 2))
    (folder / "long.fit").write_bytes(fit_at(LONG_DAY, 8.0, 10_800, 3))
    return folder


def window_with_days(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    *,
    gate: threading.Event | None = None,
) -> MainWindow:
    """A profile made from the interface, with the activities of `make_days_folder`."""
    window = compose(tmp_path, settings, gate=gate)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_days_folder(tmp_path))
    wait_ready(qtbot, window, 3)
    return window


def cell_of(window: MainWindow, day: date) -> CalendarDay:
    """The calendar cell of a day of the month shown."""
    model = window.calendar_page.model
    index = model.index_of(day)
    cell = model.day_at(index.row(), index.column())
    assert cell is not None
    return cell


def last_day_page(window: MainWindow) -> DayPage:
    """The page of the day window opened last (and still open)."""
    windows = window.nutrition.day_windows
    assert windows, "no day window is open"
    return windows[-1].page


def open_day(qtbot: QtBot, window: MainWindow, day: date, *, wait: bool = True) -> DayPage:
    """Show the calendar, select a day and press Enter: its window opens; wait for the guidance
    or what is missing. Returns the page of that window."""
    window.sidebar.setCurrentRow(2)
    page = window.calendar_page
    page.select_day(day)
    assert page.selected_day() == day
    QTest.keyClick(page.table, Qt.Key.Key_Return)
    day_window = window.nutrition.day_window(day)
    assert day_window is not None
    if wait:
        qtbot.waitUntil(
            lambda: day_window.page.state in (DayState.CONTENT, DayState.UNAVAILABLE),
            timeout=WAIT_MS,
        )
    return day_window.page


def open_settings(qtbot: QtBot, window: MainWindow) -> NutritionSettingsDialog:
    """Open Settings > Nutrition and wait for its content."""
    window.show_settings_page(SettingsCategory.NUTRITION)
    qtbot.waitUntil(lambda: window.nutrition.settings_dialog is not None, timeout=WAIT_MS)
    dialog = window.nutrition.settings_dialog
    assert dialog is not None
    return dialog


def clock_minutes(title: str) -> int:
    """Minutes after midnight of a meal card title like "Lunch at 1:00 PM"."""
    found = re.search(r"at (\d+):(\d+) (AM|PM)$", plain(title))
    assert found is not None, title
    hour, minute, meridiem = int(found[1]), int(found[2]), found[3]
    return (hour % 12 + (12 if meridiem == "PM" else 0)) * 60 + minute


def period_rows(periods: tuple[GoalPeriod, ...]) -> list[tuple[str, ...]]:
    """What the periods table should show for weight-loss periods (first, last, goal, pace)."""
    return [
        (short_date(p.first_day), "" if p.last_day is None else short_date(p.last_day))
        for p in periods
    ]
