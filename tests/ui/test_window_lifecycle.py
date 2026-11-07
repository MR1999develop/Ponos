# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Two-phase closing, window memory, log folder and error messages (phase 1)."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QListWidget, QMenu
from pytestqt.qtbot import QtBot

from ponos_coach.ui import overview_page
from ponos_coach.ui.import_report_dialog import ImportReportDialog
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Loading
from ponos_coach.ui.pmc_figure import prepare_chart
from tests.ui.test_main_window import click, import_folder_and_wait, open_message_box, two_profiles
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    gated_sources,
    make_import_folder,
    make_settings,
    wait_ready,
)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def window_importing_behind_a_gate(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> tuple[MainWindow, threading.Event]:
    """Window with an import stopped midway (the gate keeps the data thread busy)."""
    gate = threading.Event()
    window = compose(tmp_path, settings, import_sources=gated_sources(gate))
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    window.import_folder(make_import_folder(tmp_path))
    qtbot.waitUntil(
        lambda: (
            window.controller.import_progress is not None
            and window.controller.import_progress.total > 0
        ),
        timeout=WAIT_MS,
    )
    return window, gate


# --- R4: two-phase closing ---------------------------------------------------------------


def test_closing_never_blocks_the_ui_thread_and_a_busy_data_thread_is_waited_for_by_a_timer(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, gate = window_importing_behind_a_gate(qtbot, tmp_path, settings)
    release = threading.Timer(0.4, gate.set)
    release.start()

    started = time.monotonic()
    window.close()

    assert time.monotonic() - started < 0.2  # the UI thread did not wait
    assert not window.is_closed
    assert window.statusBar().currentMessage() == "Closing the profile..."
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)
    release.join()
    assert window.controller.has_stopped


def test_after_closing_starts_nothing_new_runs_not_even_the_overview_after_the_import(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, gate = window_importing_behind_a_gate(qtbot, tmp_path, settings)
    states: list[object] = []
    window.controller.overview_changed.connect(states.append)

    window.close()
    controller = window.controller

    assert controller.is_closing
    assert not controller.import_files([tmp_path])
    assert not controller.recalculate_overview()
    assert not controller.can_change_profile
    assert not controller.compute(lambda: None, lambda _v: None, lambda _m: None)
    gate.set()  # the cancelled import finishes: `_after_import` must not restart the overview
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)
    assert not [state for state in states if isinstance(state, Loading)]
    assert controller.import_progress is None


def test_a_data_thread_that_does_not_finish_in_time_leaves_the_window_open_with_a_warning(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window, gate = window_importing_behind_a_gate(qtbot, tmp_path, settings)
    window.shutdown_wait_ms = 200  # short cap: the gate stays closed

    window.close()
    box = open_message_box(qtbot, window, "shutdownTimeout")

    assert box.text() == "Ponos Coach is still closing the profile"
    assert "close the window again" in box.informativeText()
    assert not window.is_closed  # no QThread destroyed while running
    gate.set()
    click(box, "OK")
    close_window(qtbot, window)  # second attempt: now the thread finishes
    assert window.controller.has_stopped


def test_closing_a_window_without_a_profile_is_immediate(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert window.close()
    assert window.is_closed


# --- D-F1-08, D-F1-09: the window remembers ---------------------------------------------------


@pytest.mark.parametrize("saved", ["testo", "99", "-1", "4", 42, ""], ids=repr)
def test_a_saved_section_that_is_not_available_opens_the_overview(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, saved: object
) -> None:
    settings.setValue("window/section", saved)

    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None
    assert sidebar.currentRow() == 0


def test_the_section_is_written_when_it_changes_not_only_at_close(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None

    sidebar.setCurrentRow(1)

    assert settings.value("window/section", type=int) == 1


def test_a_geometry_of_the_wrong_type_does_not_stop_the_window_from_opening(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    settings.setValue("window/geometry", ["a", "list"])

    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert (window.width(), window.height()) == (1100, 720)


# --- R5: log folder and last-resort handler ----------------------------------------


def test_the_help_menu_opens_the_log_folder_with_the_system_file_manager(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[QUrl] = []

    def record_open(url: QUrl) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(record_open))
    logs = tmp_path / "logs"
    window = compose(tmp_path, settings, log_dir=logs)
    qtbot.addWidget(window)

    window.log_action.trigger()

    assert opened == [QUrl.fromLocalFile(str(logs))]
    assert (
        logs.is_dir()
    )  # if it did not exist yet it creates it: a nonexistent folder is not opened
    help_menu = window.menuBar().actions()[-1].menu()
    assert isinstance(help_menu, QMenu)
    assert window.log_action in help_menu.actions()


def test_when_the_log_folder_cannot_be_opened_the_message_says_where_it_is(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda _url: False))
    logs = tmp_path / "logs"
    window = compose(tmp_path, settings, log_dir=logs)
    qtbot.addWidget(window)

    window.log_action.trigger()

    box = open_message_box(qtbot, window, "logFolderError")
    assert box.text() == "The log folder cannot be opened"
    assert str(logs) in box.informativeText()


def test_the_unexpected_error_box_is_generic_and_shown_once_at_a_time(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.show_unexpected_error()
    window.show_unexpected_error()

    boxes = [b for b in window.open_boxes if b.objectName() == "unexpectedError"]
    assert len(boxes) == 1
    assert "Help > Open the log folder" in boxes[0].informativeText()  # type: ignore[union-attr]
    assert "Traceback" not in boxes[0].informativeText()  # type: ignore[union-attr]


def test_a_failed_job_logs_only_the_types_never_the_text_of_the_exception(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    secret = tmp_path / "Segreto_Di_Prova"

    window.open_profile_folder(secret)  # does not exist
    open_message_box(qtbot, window, "errorMessage")

    failed = [r for r in caplog.records if r.getMessage().startswith("Job ")]
    assert failed
    assert all(r.exc_info is None for r in failed)
    assert "Segreto_Di_Prova" not in "".join(r.getMessage() for r in caplog.records)


# --- R9, import and deletion outcomes ---------------------------------------------


def test_the_deletion_preview_of_an_unreadable_profile_still_asks_and_says_it_cannot_count(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)
    database = tmp_path / "profiles" / "One" / "ponos.sqlite3"
    database.write_bytes(b"not a database " * 100)

    window.delete_menu.actions()[1].trigger()

    box = open_message_box(qtbot, window, "deleteConfirmation")
    assert "The number of activities could not be read." in box.informativeText()
    assert database.read_bytes() == b"not a database " * 100  # read-only does not touch anything
    click(box, "Cancel")


def test_the_deletion_preview_counts_without_opening_or_touching_the_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)
    folder = tmp_path / "profiles" / "One"
    before = sorted(path.name for path in folder.iterdir())

    window.delete_menu.actions()[1].trigger()

    box = open_message_box(qtbot, window, "deleteConfirmation")
    assert "Activities that will be deleted: 8." in box.informativeText()
    assert sorted(path.name for path in folder.iterdir()) == before  # no lock or migrations
    click(box, "Cancel")


def test_deleting_a_folder_with_files_of_the_user_explains_that_nothing_was_deleted(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)
    folder = tmp_path / "profiles" / "One"
    (folder / "mie-note.txt").write_text("non del profilo", encoding="utf-8")

    window.delete_menu.actions()[1].trigger()
    click(open_message_box(qtbot, window, "deleteConfirmation"), "Delete folder")

    error = open_message_box(qtbot, window, "errorMessage")
    assert error.text() == "The folder was not deleted"
    assert "Nothing was deleted" in error.informativeText()
    assert (folder / "mie-note.txt").is_file()
    assert (folder / "ponos.sqlite3").is_file()


def test_reimporting_a_file_whose_archive_copy_is_gone_reports_a_repair(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    source = make_import_folder(tmp_path)
    import_folder_and_wait(qtbot, window, source)
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    archived = sorted((tmp_path / "profiles" / "Ada" / "archivio").rglob("*.fit"))
    assert archived
    archived[0].unlink()

    report = import_folder_and_wait(qtbot, window, source)

    assert report.repaired == 1
    assert report.imported == 0
    dialogs = [box for box in window.open_boxes if isinstance(box, ImportReportDialog)]
    dialog = dialogs[-1]  # the last summary
    assert "Archived files repaired from the imported original: 1" in dialog.summary_text


# --- R14: the profile list unusable ------------------------------------------------


def test_a_corrupt_profile_list_says_where_the_file_is_and_how_to_start_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "profiles.json").write_text("{ non json", encoding="utf-8")
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.start()

    box = open_message_box(qtbot, window, "errorMessage")
    assert box.text() == "The list of profiles cannot be read"
    assert "profiles.json" in box.informativeText()
    assert str(config) in box.informativeText()
    assert "move profiles.json out of that folder" in box.informativeText()
    assert window.new_profile_action.isEnabled()  # the window stays usable


def test_a_profile_list_from_a_newer_version_says_to_update_the_app(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "profiles.json").write_text(
        '{"format_version": 99, "profiles": []}', encoding="utf-8"
    )
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.start()

    box = open_message_box(qtbot, window, "errorMessage")
    assert box.text() == "The list of profiles is from a newer version"
    assert "Update Ponos Coach" in box.informativeText()


# --- D-F1-02 and D-F1-10 -----------------------------------------------------------------------


def test_the_orphan_temporaries_removed_at_opening_are_logged_as_a_count_only(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    close_window(qtbot, window)
    year = tmp_path / "profiles" / "Ada" / "archivio" / "2026"
    year.mkdir(parents=True)
    for number in range(2):
        (year / f".tmp-{number:032x}").write_bytes(b"interrotta")

    again = compose(tmp_path, settings)
    qtbot.addWidget(again)
    again.start()
    qtbot.waitUntil(lambda: again.controller.profile is not None, timeout=WAIT_MS)

    assert list(year.iterdir()) == []
    messages = [r.getMessage() for r in caplog.records]
    assert "Removed 2 orphan temporary file(s) of the archive" in messages
    assert not [m for m in messages if ".tmp-" in m or "Ada" in m]


def test_the_chart_figure_is_built_outside_the_ui_thread(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: list[int] = []

    def recording(*args: object, **kwargs: object) -> object:
        threads.append(threading.get_ident())
        return prepare_chart(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(overview_page, "prepare_chart", recording)
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    import_folder_and_wait(qtbot, window, make_import_folder(tmp_path))
    chart = window.overview_page.chart

    qtbot.waitUntil(lambda: chart.current_figure is not None, timeout=WAIT_MS)

    assert threads
    assert threading.get_ident() not in threads
    assert window.overview_page.current_widget() is window.overview_page.charts_widget


def test_the_web_engine_starts_after_the_first_show_not_in_the_constructor(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    chart = window.overview_page.chart

    assert not chart.engine_started  # building the window does not cost the 180 ms of Chromium

    window.show()

    qtbot.waitUntil(lambda: chart.engine_started, timeout=WAIT_MS)
