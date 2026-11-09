# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for the nutrition data of phase 4b: calories, meal times, goals, level and body data
never in the logs, the messages or the error texts.

The real window is driven with invented, recognisable values (no real person): a FIT with declared
calories, the activity level, three meal times, a weight loss pace; then the failures that could
leak them: a store that raises an exception whose text holds the values, a day whose calculation
raises, a period refused by the store. Every value is searched for in the log file, in the captured
records (message, arguments, traceback) and in the texts the user is shown.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QSettings, QTime
from pytestqt.qtbot import QtBot
from pytestqt.wait_signal import SignalBlocker

from ponos_coach.nutrition.domain import ActivityLevel, GoalPeriod, MaintenanceGoal
from ponos_coach.profiles.day_nutrition import PlanDayNutrition
from ponos_coach.storage.sqlite_nutrition import SqliteMealTimesStore
from ponos_coach.ui.day_page import DayState
from ponos_coach.ui.diagnostics import LOG_FILE_NAME, LOGGER_NAME, SafeFormatter, configure_logging
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.nutrition_settings_model import MealField
from ponos_coach.ui.settings_page import SettingsCategory
from tests.collaudo.support import format_logs
from tests.collaudo.test_fase3_log_salute import box_texts, strip_timestamps, visible_boxes
from tests.ui.nutrition_support import fit_at, last_day_page, open_settings
from tests.ui.workspace_support import (
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    make_settings,
    wait_ready,
)

CALORIES = 1377  # declared by the device
MEAL_MINUTES = (6 * 60 + 47, 12 * 60 + 41, 19 * 60 + 53)  # 06:47, 12:41, 19:53
PACE_KG = 0.73
LEAKY = "meal times 06:47 weight 65.5 kg and 1377 kcal, loss 0.73"
DAY = date(2026, 6, 10)

FORBIDDEN = {
    "calorie": re.compile(r"(?<![\w.])1377(?![\w])"),
    "orari": re.compile(r"0?6:47|12:41|19:53|(?<!\d)(407|761|1193)(?!\d)"),
    "ritmo": re.compile(r"0[.,]73|(?<!\d)730(?!\d)"),
    "peso": re.compile(r"65[.,]5|(?<!\d)655(?!\d)"),
    "livello": re.compile(r"very_active|VERY_ACTIVE|Very active", re.IGNORECASE),
    "obiettivo": re.compile(r"weight_loss|weight loss|WeightLoss", re.IGNORECASE),
    "nascita": re.compile(r"1995|04-02"),
    "eccezione": re.compile(r"meal times 06|weight 65|loss 0\.73"),
}


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def log_file(tmp_path: Path) -> Iterator[Path]:
    """The log file as `main()` configures it, removed at the end of the test."""
    path = configure_logging(tmp_path / "logs", version="collaudo")
    assert path is not None
    yield path
    logger = logging.getLogger(LOGGER_NAME)
    for handler in [h for h in logger.handlers if getattr(h, "_ponos_coach_handler", False)]:
        logger.removeHandler(handler)
        handler.close()


def window_with_calories(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    folder = tmp_path / "days"
    folder.mkdir()
    (folder / "run.fit").write_bytes(fit_at(DAY, 6.0, 1800, 1, calories=CALORIES))
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Atleta di prova")
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(folder)
    wait_ready(qtbot, window, 1)
    return window


def changed(qtbot: QtBot, window: MainWindow) -> SignalBlocker:
    return qtbot.waitSignal(window.nutrition.loader.settings_changed, timeout=WAIT_MS)


def enter_the_values(qtbot: QtBot, window: MainWindow) -> None:
    """The level, meal times and a weight loss period, through the dialog."""
    dialog = open_settings(qtbot, window)
    dialog.level_combo.setCurrentIndex(dialog.level_combo.findData(ActivityLevel.VERY_ACTIVE))
    dialog.level_from_edit.setDate(QDate(2026, 6, 1))
    with changed(qtbot, window):
        dialog.set_level_button.click()
    for field, minute in zip(MealField, MEAL_MINUTES, strict=True):
        dialog.time_edits[field].setTime(QTime(minute // 60, minute % 60))
    with changed(qtbot, window):
        dialog.save_times_button.click()
    dialog.add_period()
    sub = dialog.period_dialog
    assert sub is not None
    sub.set_dates(DAY - timedelta(days=2), DAY + timedelta(days=4))
    sub.pace_spin.setValue(PACE_KG)
    with changed(qtbot, window):
        sub.save_button.click()


def drive_the_failures(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """The failures that could leak the values; gives the texts the user saw."""
    shown: list[str] = []
    dialog = window.nutrition.settings_dialog
    assert dialog is not None
    # 1. the store refuses a period that overlaps (a known failure, with its own text)
    clash = GoalPeriod(DAY, DAY + timedelta(days=1), MaintenanceGoal())
    with qtbot.waitSignal(window.nutrition.loader.command_failed, timeout=WAIT_MS):
        window.nutrition.loader.add_period(clash)
    qtbot.waitUntil(lambda: dialog.failure_text != "", timeout=WAIT_MS)
    shown.append(dialog.failure_text)

    # 2. an unexpected exception whose text holds the values, while saving the meal times
    def leaky_store(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(SqliteMealTimesStore, "set", leaky_store)
    dialog.time_edits[MealField.DINNER].setTime(QTime(21, 5))
    with qtbot.waitSignal(window.nutrition.loader.command_failed, timeout=WAIT_MS):
        dialog.save_times_button.click()
    qtbot.waitUntil(lambda: dialog.failure_text != "", timeout=WAIT_MS)
    shown += [dialog.failure_text, box_texts()]
    monkeypatch.undo()
    window.settings_window.page(SettingsCategory.NUTRITION).discard()  # the failed times go away

    # 3. the calculation of a day raises an exception with the values in its text
    def leaky_day(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(PlanDayNutrition, "__call__", leaky_day)
    window.nutrition.open_day(DAY)
    qtbot.waitUntil(lambda: last_day_page(window).state is DayState.FAILED, timeout=WAIT_MS)
    shown += [*last_day_page(window).failure_texts, box_texts()]
    monkeypatch.undo()
    for box in visible_boxes():
        box.close()
    return shown


def test_t_sec_08_no_nutrition_datum_reaches_the_log_the_messages_or_the_error_texts(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="ponos_coach"):
        window = window_with_calories(qtbot, tmp_path, settings)
        enter_the_values(qtbot, window)
        shown = drive_the_failures(qtbot, window, monkeypatch)
        window.nutrition.open_day(DAY)  # the day computed for real, with every value in force
        qtbot.waitUntil(lambda: last_day_page(window).state is DayState.CONTENT, timeout=WAIT_MS)
        close_window(qtbot, window)

    log_file = tmp_path / "logs" / LOG_FILE_NAME
    on_disk = strip_timestamps(log_file.read_text(encoding="utf-8")) if log_file.exists() else ""
    formatter = SafeFormatter()
    everywhere = {
        "file di log": on_disk,
        "lines of the safe formatter": strip_timestamps(
            "\n".join(formatter.format(r) for r in caplog.records)
        ),
        "messaggi grezzi": "\n".join(r.getMessage() for r in caplog.records),
        "messaggi e traceback": strip_timestamps(format_logs(caplog.records)),
        "testi mostrati": "\n".join(shown),
    }
    assert on_disk  # the log file exists and has written something (the failed jobs)
    assert "RuntimeError" in on_disk  # the type is logged, never the text
    leaks = {
        (where, kind, match.group(0))
        for where, text in everywhere.items()
        for kind, pattern in FORBIDDEN.items()
        for match in pattern.finditer(text)
        # pytest's own traceback holds the text of the exception it is about to show
        if not (where == "messaggi e traceback" and kind == "eccezione")
    }
    assert leaks == set()


def test_t_sec_08_the_day_failure_shows_a_generic_message_and_no_exception_text(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = window_with_calories(qtbot, tmp_path, settings)

    def leaky_day(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(PlanDayNutrition, "__call__", leaky_day)
    window.nutrition.open_day(DAY)
    qtbot.waitUntil(lambda: last_day_page(window).state is DayState.FAILED, timeout=WAIT_MS)
    title, detail = last_day_page(window).failure_texts
    monkeypatch.undo()

    assert title.strip() and detail.strip()
    assert "RuntimeError" not in title + detail
    assert not FORBIDDEN["eccezione"].search(title + detail)
    close_window(qtbot, window)


def test_t_sec_08_a_day_that_computes_logs_nothing_about_the_person(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="ponos_coach"):
        window = window_with_calories(qtbot, tmp_path, settings)
        enter_the_values(qtbot, window)
        before = len(caplog.records)
        window.nutrition.open_day(DAY)
        qtbot.waitUntil(lambda: last_day_page(window).state is DayState.CONTENT, timeout=WAIT_MS)
        day_records = caplog.records[before:]
        close_window(qtbot, window)

    assert [r for r in day_records if r.levelno >= logging.INFO] == []


@pytest.mark.parametrize(
    ("kind", "sample"),
    [
        ("calorie", "energy 1377 kcal"),
        ("orari", "dinner at 19:53"),
        ("orari", "breakfast minute 407"),
        ("ritmo", "pace 0,73 kg"),
        ("ritmo", "pace 730 g"),
        ("peso", "weight 65.5"),
        ("livello", "level=VERY_ACTIVE"),
        ("obiettivo", "goal weight_loss"),
        ("nascita", "born 1995-04-02"),
        ("eccezione", LEAKY),
    ],
)
def test_t_sec_08_the_detector_sees_every_value_it_looks_for(kind: str, sample: str) -> None:
    """A search that cannot find anything proves nothing: each pattern matches a leaking text."""
    assert FORBIDDEN[kind].search(sample)
