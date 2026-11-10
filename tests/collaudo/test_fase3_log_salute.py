# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 for the personal data of phase 3: height, weight, date of birth and HR never in the
logs.

The real interface is run through with invented, recognisable values (no real person):
profile creation, measurement edit, refused inputs, a database failure
and an unexpected exception whose text contains the values. Then every value is searched for in
everything
the app writes or shows: the log file, the captured records (message, arguments, traceback)
and the texts of the messages and of the error fields.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QSpinBox
from pytestqt.qtbot import QtBot

from ponos_coach.profiles.ports import ProfileDataError
from ponos_coach.storage import sqlite_profile_data
from ponos_coach.ui.diagnostics import LOG_FILE_NAME, LOGGER_NAME, SafeFormatter, configure_logging
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.measurements_form import EditField
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.settings_page import SettingsCategory
from tests.collaudo.support import format_logs
from tests.ui.test_profile_data_window import (
    PROFILE,
    click,
    open_edit_dialog,
)
from tests.ui.workspace_support import (
    WAIT_MS,
    close_window,
    compose,
    fill_body_data,
    make_settings,
    pick_profile_folder,
    wait_profile_saved,
)

HEIGHT = "187"
WEIGHT_TYPED = "93,7"
BIRTH = date(1983, 11, 27)
FIRST_WEIGHT_UPDATE = "88,3"
MIN_BPM, MAX_BPM, THRESHOLD_BPM = 43, 199, 181
LEAKY = "the weight 77.7 kg and height 191 cm"  # the text of an exception that contains data

FORBIDDEN = {
    "altezza": re.compile(r"(?<![\w.])187(?![\w])"),
    "peso": re.compile(r"93[.,]7|88[.,]3|(?<!\d)937(?!\d)|(?<!\d)883(?!\d)"),
    "nascita": re.compile(r"1983|11-27|27/11|11/27|27\.11"),
    "FC": re.compile(rf"(?<![\w.:-])({MIN_BPM}|{MAX_BPM}|{THRESHOLD_BPM})(?![\w.:-])"),
    "eccezione": re.compile(r"77\.7|191 cm|weight 77"),
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


def strip_timestamps(text: str) -> str:
    """Removes the initial date and time of every line of the log file (clock digits)."""
    return "\n".join(re.sub(r"^\S+T\S+Z ", "", line) for line in text.splitlines())


def visible_boxes() -> list[QMessageBox]:
    """Every visible message of the app, however it was opened."""
    return [
        w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()
    ]


def box_texts() -> str:
    parts: list[str] = []
    for box in visible_boxes():
        parts += [box.text(), box.informativeText(), box.detailedText(), box.windowTitle()]
    return "\n".join(parts)


def error_texts(dialog: ProfileEditDialog) -> str:
    """The error messages under the fields (not the data the dialog shows the user)."""
    return "\n".join(dialog.error_text(field) for field in EditField)


def create_with_sentinels(qtbot: QtBot, window: MainWindow, tmp_path: Path) -> None:
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    QTest.keyClicks(dialog.name_edit, PROFILE)
    fill_body_data(dialog, HEIGHT, WEIGHT_TYPED, BIRTH)
    for name, value in (
        ("maxHeartRate", MAX_BPM),
        ("minHeartRate", MIN_BPM),
        ("thresholdHeartRate", THRESHOLD_BPM),
    ):
        box = dialog.findChild(QSpinBox, name)
        assert box is not None
        box.setValue(value)
    pick_profile_folder(window, dialog, tmp_path / "profiles" / PROFILE)
    click(dialog.create_button)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)


def drive_the_interface(
    qtbot: QtBot, window: MainWindow, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    """Valid edits, refused inputs, and failures; gives the texts the user saw."""
    shown: list[str] = []
    # 1. valid edit of the measurements
    edit = open_edit_dialog(qtbot, window)
    edit.weight_edit.clear()
    QTest.keyClicks(edit.weight_edit, FIRST_WEIGHT_UPDATE)
    click(window.settings_window.save_button)
    wait_profile_saved(qtbot, window)

    # 2. inputs refused on the spot: non-numeric text, weight out of range, inconsistent HR
    edit = open_edit_dialog(qtbot, window)
    for text in ("abc", "19,9", "301", "93,77"):
        edit.weight_edit.clear()
        QTest.keyClicks(edit.weight_edit, text)
        click(window.settings_window.save_button)
        shown.append(error_texts(edit))
    edit.weight_edit.clear()
    edit.min_box.setValue(MAX_BPM)  # minimum above the maximum
    edit.threshold_box.setValue(THRESHOLD_BPM)
    click(window.settings_window.save_button)
    shown.append(error_texts(edit))

    # 3. database failure (known save error) and then an unexpected exception with data
    def known_failure(*_args: object) -> None:
        raise ProfileDataError("weight write failed")

    def leaky_failure(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    window.settings_window.page(SettingsCategory.PROFILE).discard()
    for failure in (known_failure, leaky_failure):
        monkeypatch.setattr(sqlite_profile_data.SqliteWeightStore, "put", failure)
        again = open_edit_dialog(qtbot, window)
        again.weight_edit.clear()
        QTest.keyClicks(again.weight_edit, "91,5")
        click(window.settings_window.save_button)
        qtbot.waitUntil(lambda: bool(visible_boxes()), timeout=WAIT_MS)
        shown.append(box_texts())
        shown.append(error_texts(again))
        for box in visible_boxes():
            box.close()
        monkeypatch.undo()
        window.settings_window.page(SettingsCategory.PROFILE).discard()

    return shown


def test_t_sec_08_no_body_datum_reaches_the_log_the_messages_or_the_error_texts(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="ponos_coach"):
        window = compose(tmp_path, settings)
        qtbot.addWidget(window)
        create_with_sentinels(qtbot, window, tmp_path)

        shown = drive_the_interface(qtbot, window, monkeypatch)
        close_window(qtbot, window)

    log_file = tmp_path / "logs" / LOG_FILE_NAME
    on_disk = strip_timestamps(log_file.read_text(encoding="utf-8")) if log_file.exists() else ""
    formatter = SafeFormatter()
    safe_lines = strip_timestamps("\n".join(formatter.format(r) for r in caplog.records))
    raw_messages = "\n".join(r.getMessage() for r in caplog.records)
    raw_with_traceback = strip_timestamps(format_logs(caplog.records))
    everywhere = {
        "file di log": on_disk,
        "lines of the safe formatter": safe_lines,
        "messaggi grezzi": raw_messages,
        "messaggi e traceback": raw_with_traceback,
        "testi mostrati": "\n".join(shown),
    }
    assert log_file.exists() and on_disk  # the log file exists and has written something
    leaks = {
        (where, kind, match.group(0))
        for where, text in everywhere.items()
        for kind, pattern in FORBIDDEN.items()
        for match in pattern.finditer(text)
        # the raw traceback necessarily contains the text of the unexpected exception: it is the
        # safe formatter (the file's one) that must stay silent, not pytest's traceback
        if not (where == "messaggi e traceback" and kind == "eccezione")
    }
    assert leaks == set()


def test_t_sec_08_the_last_resort_handler_shows_a_generic_message_and_logs_only_the_type(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    create_with_sentinels(qtbot, window, tmp_path)

    def leaky(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    monkeypatch.setattr(sqlite_profile_data.SqliteWeightStore, "put", leaky)
    edit = open_edit_dialog(qtbot, window)
    edit.weight_edit.clear()
    QTest.keyClicks(edit.weight_edit, FIRST_WEIGHT_UPDATE)
    click(window.settings_window.save_button)
    qtbot.waitUntil(lambda: bool(visible_boxes()), timeout=WAIT_MS)
    texts = box_texts()
    monkeypatch.undo()

    assert not FORBIDDEN["eccezione"].search(texts)
    assert "RuntimeError" not in texts  # no class name shown to the user
    assert texts.strip()  # but a message is there
    for box in visible_boxes():
        box.close()
    window.settings_window.page(SettingsCategory.PROFILE).discard()
    close_window(qtbot, window)
    log = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "RuntimeError" in log  # the detail for whoever investigates: the type
    assert not FORBIDDEN["eccezione"].search(log)  # never the text
