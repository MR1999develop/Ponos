# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-01, T-UI-02, T-UI-03, T-UI-07: dal Composition Root, profili, import e panoramica."""

from __future__ import annotations

import threading
import time
import uuid
import warnings
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QListWidget, QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import main, resolve_user_timezone
from ponos_coach.activities.ports import (
    ImportEntry,
    ImportLimit,
    ImportLimitExceededError,
    ImportScan,
)
from ponos_coach.profiles.domain import FolderState, ProfileId, ProfileName
from ponos_coach.profiles.use_cases import FolderWarning
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.import_report_dialog import ImportReportDialog
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import Field
from ponos_coach.ui.overview_state import Cancelled, Idle, Loading, Ready
from ponos_coach.ui.workspace import ImportSources, ProfileView
from tests.fixtures.generators.import_files import fit_run
from tests.ui.workspace_support import (
    DAYS_IN_CHART,
    FILES_IN_FIXTURE_FOLDER,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    fill_new_profile,
    gated_sources,
    make_import_folder,
    make_settings,
    pick_profile_folder,
    picker_of,
    wait_ready,
)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def import_folder_and_wait(qtbot: QtBot, window: MainWindow, folder: Path) -> ImportReport:
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.import_folder(folder)
    report = blocker.args[0] if blocker.args else None
    assert isinstance(report, ImportReport)
    return report


def open_message_box(qtbot: QtBot, window: MainWindow, name: str) -> QMessageBox:
    def find() -> QMessageBox | None:
        return next(
            (
                box
                for box in window.open_boxes
                if isinstance(box, QMessageBox) and box.objectName() == name
            ),
            None,
        )

    qtbot.waitUntil(lambda: find() is not None, timeout=WAIT_MS)
    box = find()
    assert box is not None
    return box


def click(box: QMessageBox, text: str) -> None:
    next(button for button in box.buttons() if button.text() == text).click()


# --- Primo avvio, creazione, import, tabella e panoramica -------------------------------------


def test_first_run_creates_a_profile_imports_a_folder_and_updates_table_and_overview(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()

    create_profile(qtbot, window, "Ada")

    assert window.windowTitle() == "Ada - Ponos Coach"
    assert window.controller.profile is not None
    assert window.controller.profile.folder == tmp_path / "profiles" / "Ada"
    page = window.overview_page
    assert page.current_widget() is page.state_page
    assert page.state_page.button.text() == "Import files..."
    assert window.import_files_action.isEnabled()

    folder = make_import_folder(tmp_path)
    report = import_folder_and_wait(qtbot, window, folder)

    assert report.imported == FILES_IN_FIXTURE_FOLDER
    assert report.failed == 1
    assert [item.name for item in report.not_imported] == ["bad.fit"]
    dialog = next(box for box in window.open_boxes if isinstance(box, ImportReportDialog))
    assert dialog.table.model().rowCount() == 1
    assert "8 activity(ies) imported" in dialog.summary_text

    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    assert window.activities_page.model.rowCount() == FILES_IN_FIXTURE_FOLDER
    assert window.status_texts == (
        "8 activity(ies)",
        "0 excluded activity(ies)",
        f"{DAYS_IN_CHART} day(s) in the chart",
    )
    chart = window.overview_page.chart
    qtbot.waitUntil(
        lambda: chart.current_figure is not None, timeout=WAIT_MS
    )  # figura dal thread dei dati
    figure = chart.current_figure
    assert figure is not None
    assert len(figure.data[0].x) == DAYS_IN_CHART
    assert page.current_widget() is page.chart
    assert settings.value("import/lastFolder", "", type=str) == str(folder)
    assert not window.import_status_text or not window.controller.import_progress


def test_the_last_profile_reopens_at_the_next_start(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    first = compose(tmp_path, settings)
    qtbot.addWidget(first)
    first.start()
    create_profile(qtbot, first, "Ada")
    import_folder_and_wait(qtbot, first, make_import_folder(tmp_path))
    wait_ready(qtbot, first, FILES_IN_FIXTURE_FOLDER)
    first.resize(800, 580)
    close_window(qtbot, first)  # chiude il profilo nel thread dei dati: il lock si rilascia

    second = compose(tmp_path, settings)
    qtbot.addWidget(second)
    second.start()

    qtbot.waitUntil(lambda: second.controller.profile is not None, timeout=WAIT_MS)
    wait_ready(qtbot, second, FILES_IN_FIXTURE_FOLDER)
    assert second.windowTitle() == "Ada - Ponos Coach"
    assert second.new_profile_dialog is None  # niente primo avvio: un profilo c'è
    assert second.activities_page.model.rowCount() == FILES_IN_FIXTURE_FOLDER
    assert (second.width(), second.height()) == (800, 580)


def test_a_profile_open_in_another_window_gives_an_understandable_message(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    first = compose(tmp_path, settings)
    qtbot.addWidget(first)
    first.start()
    create_profile(qtbot, first, "Ada")

    second = compose(tmp_path, QSettings(str(tmp_path / "second.ini"), QSettings.Format.IniFormat))
    qtbot.addWidget(second)
    second.start()

    box = open_message_box(qtbot, second, "errorMessage")
    assert box.text() == "The profile is already open"
    assert "another window" in box.informativeText()
    assert str(tmp_path) not in box.informativeText()  # mai percorsi nei messaggi
    assert second.controller.profile is None
    assert isinstance(second.controller.overview_state, Idle)
    assert second.overview_page.current_widget() is second.overview_page.state_page


# --- Più profili: cambio, rimozione dall'elenco, eliminazione ----------------------------------


def two_profiles(qtbot: QtBot, window: MainWindow) -> None:
    window.start()
    create_profile(qtbot, window, "One")
    folder = make_import_folder(window.controller.profile.folder.parent)  # type: ignore[union-attr]
    import_folder_and_wait(qtbot, window, folder)
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    create_profile(qtbot, window, "Two")


def menu_texts(menu_actions: list[QAction]) -> list[tuple[str, bool]]:
    return [(action.text(), action.isEnabled()) for action in menu_actions]


def test_switching_profiles_lists_the_most_recent_first_and_opens_the_chosen_one(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)

    assert menu_texts(window.switch_menu.actions()) == [("Two", False), ("One", True)]
    window.switch_menu.actions()[1].trigger()

    qtbot.waitUntil(
        lambda: (
            window.windowTitle() == "One - Ponos Coach"
            and isinstance(window.controller.overview_state, Ready)
        ),
        timeout=WAIT_MS,
    )
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    assert window.activities_page.model.rowCount() == FILES_IN_FIXTURE_FOLDER
    assert menu_texts(window.switch_menu.actions()) == [("One", False), ("Two", True)]


def test_removing_a_profile_from_the_list_keeps_its_data(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)

    window.remove_menu.actions()[1].trigger()  # "One"

    qtbot.waitUntil(lambda: len(window.remove_menu.actions()) == 1, timeout=WAIT_MS)
    assert [a.text() for a in window.switch_menu.actions()] == ["Two"]
    assert (tmp_path / "profiles" / "One" / "ponos.sqlite3").is_file()


def test_deleting_a_profile_folder_asks_with_friction_and_only_then_deletes(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    two_profiles(qtbot, window)
    folder = tmp_path / "profiles" / "One"

    # Il profilo aperto non si può eliminare.
    assert menu_texts(window.delete_menu.actions()) == [("Two", False), ("One", True)]
    window.delete_menu.actions()[1].trigger()
    box = open_message_box(qtbot, window, "deleteConfirmation")

    assert 'profile "One"' in box.text()
    assert str(folder) in box.informativeText()
    assert "8 activity(ies) will be deleted." in box.informativeText()
    assert box.defaultButton().text() == "Cancel"  # il bottone sicuro è quello predefinito
    assert box.escapeButton().text() == "Cancel"
    click(box, "Cancel")
    qtbot.waitUntil(lambda: not box.isVisible(), timeout=WAIT_MS)
    assert folder.is_dir()

    window.delete_menu.actions()[1].trigger()
    box = open_message_box(qtbot, window, "deleteConfirmation")
    click(box, "Delete folder")

    qtbot.waitUntil(lambda: not folder.exists(), timeout=WAIT_MS)
    qtbot.waitUntil(lambda: len(window.delete_menu.actions()) == 1, timeout=WAIT_MS)
    assert [a.text() for a in window.switch_menu.actions()] == ["Two"]


# --- Dialogo "Nuovo profilo" -------------------------------------------------------------------


def open_first_run_dialog(qtbot: QtBot, window: MainWindow) -> NewProfileDialog:
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    return dialog


def test_the_new_profile_form_validates_in_place_and_estimates_the_threshold(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    assert dialog.threshold_note_text.startswith("Estimated at 90%")  # da confermare col test
    assert dialog.error_text(Field.NAME) == ""  # nessun errore prima del primo invio
    assert dialog.folder_edit.text() == ""  # nessuna proposta: la cartella si chiede

    pick_profile_folder(window, dialog, tmp_path / "profiles" / "A")
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    assert dialog.error_text(Field.NAME) == "Enter a name for the profile."
    assert window.controller.profile is None
    QTest.keyClicks(dialog.name_edit, "A")
    assert dialog.error_text(Field.NAME) == ""  # poi si aggiorna mentre si scrive


def test_the_folder_field_starts_empty_and_creation_waits_for_a_folder(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    dialog = open_first_run_dialog(qtbot, window)

    assert dialog.folder_edit.text() == ""
    assert not dialog.create_button.isEnabled()
    assert dialog.create_button.toolTip() == (
        "Choose the folder where the profile data will be saved."
    )
    pick_profile_folder(window, dialog, tmp_path / "chosen")
    assert dialog.create_button.isEnabled()
    assert dialog.create_button.toolTip() == ""


def test_an_empty_or_missing_folder_is_the_profile_folder(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    empty = tmp_path / "empty"
    empty.mkdir()
    QTest.keyClicks(dialog.name_edit, "Ada")

    pick_profile_folder(window, dialog, empty)
    qtbot.waitUntil(lambda: dialog.form.folder_state is FolderState.EMPTY, timeout=WAIT_MS)

    assert dialog.form.destination == str(empty)
    assert dialog.destination_text == ""
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.controller.profile is not None, timeout=WAIT_MS)
    assert window.controller.profile is not None
    assert window.controller.profile.folder == empty


def test_a_folder_with_files_gets_a_subfolder_named_after_the_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "something.txt").write_text("x", encoding="utf-8")
    QTest.keyClicks(dialog.name_edit, "Ada")

    pick_profile_folder(window, dialog, busy)
    qtbot.waitUntil(lambda: dialog.destination_text != "", timeout=WAIT_MS)

    assert dialog.destination_text == f"Data will go in: {busy / 'Ada'}"
    assert dialog.create_button.isEnabled()
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.controller.profile is not None, timeout=WAIT_MS)
    assert window.controller.profile is not None
    assert window.controller.profile.folder == busy / "Ada"
    assert (busy / "something.txt").is_file()  # il contenuto esistente non si tocca


def test_changing_the_name_updates_the_destination_subfolder(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    busy = tmp_path / "busy"
    busy.mkdir()
    (busy / "something.txt").write_text("x", encoding="utf-8")
    QTest.keyClicks(dialog.name_edit, "Ada")
    pick_profile_folder(window, dialog, busy)
    qtbot.waitUntil(lambda: dialog.destination_text != "", timeout=WAIT_MS)

    QTest.keyClicks(dialog.name_edit, " B")
    assert dialog.destination_text == f"Data will go in: {busy / 'Ada B'}"
    dialog.name_edit.clear()
    dialog.name_edit.textEdited.emit("CON")
    assert dialog.destination_text == f"Data will go in: {busy / 'CON_profile'}"


def test_the_last_profile_folder_is_remembered_as_the_start_of_the_picker(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    picker = picker_of(window)

    pick_profile_folder(window, dialog, tmp_path / "somewhere" / "mine")

    assert picker.starts == [str(tmp_path / "documents")]  # la prima volta: Documenti
    dialog.reject()
    qtbot.waitUntil(lambda: window.new_profile_dialog is None, timeout=WAIT_MS)
    window.show_new_profile()
    reopened = window.new_profile_dialog
    assert reopened is not None
    pick_profile_folder(window, reopened, tmp_path / "other")
    assert picker.starts[-1] == str(tmp_path / "somewhere")  # poi l'ultima usata


def test_a_failed_creation_reopens_the_form_with_the_data_already_typed(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_first_run_dialog(qtbot, window)
    QTest.keyClicks(dialog.name_edit, "Ada")
    folder = tmp_path / "profiles" / "Ada"
    QTest.keyClicks(dialog.folder_edit, str(folder))  # scritta a mano: nessun esame prima
    folder.mkdir(parents=True)
    (folder / "late.txt").write_text("x", encoding="utf-8")  # compare dopo l'esame del modulo
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    box = open_message_box(qtbot, window, "errorMessage")

    assert box.text() == "The folder is not empty"
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    reopened = window.new_profile_dialog
    assert reopened is not None
    assert reopened.name_edit.text() == "Ada"


def test_folder_warnings_are_shown_without_blocking(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    view = ProfileView(
        ProfileId(uuid.uuid4()),
        ProfileName("Ada"),
        tmp_path,
        "UTC",
        (FolderWarning.NETWORK,),
    )

    window.controller.profile_changed.emit(view)

    assert window.folder_warning_text.startswith("Network folder")
    assert window.controller.can_change_profile  # l'avviso non blocca nulla


# --- Reattività, annullamento, chiusura --------------------------------------------------------


def latency_of_one_event(qtbot: QtBot) -> float:
    started = time.monotonic()
    handled: list[float] = []
    QTimer.singleShot(0, lambda: handled.append(time.monotonic() - started))
    qtbot.waitUntil(lambda: bool(handled), timeout=1000)
    return handled[0]


def test_ui_stays_responsive_during_import_and_stop_cancels_it_then_import_resumes(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    gate = threading.Event()
    window = compose(tmp_path, settings, import_sources=gated_sources(gate))
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    folder = make_import_folder(tmp_path)

    window.import_folder(folder)
    qtbot.waitUntil(
        lambda: (
            window.controller.import_progress is not None
            and window.controller.import_progress.total > 0
        ),
        timeout=WAIT_MS,
    )

    assert not window.import_files_action.isEnabled()
    assert window.import_files_action.toolTip()  # il motivo
    assert not window.new_profile_action.isEnabled()
    assert window.stop_action.isEnabled()
    assert "of 9 file(s)" in window.import_status_text
    assert latency_of_one_event(qtbot) < 0.1  # T-UI-02

    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.stop_action.trigger()
        gate.set()
    cancelled = blocker.args[0] if blocker.args else None
    assert isinstance(cancelled, ImportReport)
    assert cancelled.cancelled
    assert cancelled.examined < 9
    qtbot.waitUntil(lambda: window.controller.can_import, timeout=WAIT_MS)

    resumed = import_folder_and_wait(qtbot, window, folder)  # riprende senza duplicati
    assert not resumed.cancelled
    assert cancelled.imported + resumed.imported == FILES_IN_FIXTURE_FOLDER
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)


def test_ui_stays_responsive_while_the_overview_is_calculated_and_can_be_stopped(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    gate = threading.Event()
    window = compose(tmp_path, settings, gate=gate)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")
    import_folder_and_wait(qtbot, window, make_import_folder(tmp_path))

    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Loading), timeout=WAIT_MS)
    qtbot.waitUntil(lambda: window.controller.overview_state == Loading(0, 8), timeout=WAIT_MS)
    assert window.stop_action.isEnabled()
    assert latency_of_one_event(qtbot) < 0.1  # T-UI-02

    window.stop_action.trigger()
    gate.set()

    qtbot.waitUntil(
        lambda: isinstance(window.controller.overview_state, Cancelled), timeout=WAIT_MS
    )
    page = window.overview_page
    assert page.current_widget() is page.state_page
    assert page.state_page.button.text() == "Calculate again"
    page.state_page.button.click()
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)


def test_closing_the_window_waits_for_the_data_thread(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
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
    release = threading.Timer(0.3, gate.set)
    release.start()

    start = time.monotonic()
    window.close()

    assert not window.is_closed  # primo tempo: la finestra non si blocca ad attendere il thread
    assert time.monotonic() - start < 0.25
    qtbot.waitUntil(lambda: window.is_closed, timeout=WAIT_MS)  # secondo tempo: thread finito
    release.join()
    again = compose(tmp_path, settings)
    qtbot.addWidget(again)
    again.start()
    qtbot.waitUntil(lambda: again.controller.profile is not None, timeout=WAIT_MS)  # lock libero


class LimitSource:
    """Sorgente che supera un tetto all'esame."""

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:  # noqa: ARG002
        raise ImportLimitExceededError(ImportLimit.ACTIVITY_FILES, 50_000)

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:  # noqa: ARG002
        raise AssertionError

    def close(self) -> None:
        return None


def test_exceeding_a_limit_invites_to_choose_a_subfolder_and_imports_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    sources = ImportSources(files=lambda _p: LimitSource(), folder=lambda _f: LimitSource())
    window = compose(tmp_path, settings, import_sources=sources)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, "Ada")

    window.import_folder(tmp_path)

    box = open_message_box(qtbot, window, "errorMessage")
    assert box.text() == "The folder is too large to import at once"
    assert "50000 activity file(s)" in box.informativeText()
    assert "Choose a subfolder" in box.informativeText()
    wait_ready(qtbot, window, 0)
    assert window.activities_page.model.rowCount() == 0


# --- Trascinamento --------------------------------------------------------------------------


def mime_of(paths: list[Path]) -> QMimeData:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drag_enter_event(mime: QMimeData) -> QDragEnterEvent:
    """Evento di ingresso del trascinamento (il costruttore di Qt 6 è deprecato, non ha ersatz)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return QDragEnterEvent(
            QPoint(5, 5),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )


def test_dropping_files_on_the_window_imports_them(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    mime = mime_of([tmp_path])
    refused = drag_enter_event(mime)
    window.dragEnterEvent(refused)
    assert not refused.isAccepted()  # senza profilo non si importa
    dialog = window.new_profile_dialog
    assert dialog is not None
    fill_new_profile(window, dialog, "Ada")
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.controller.can_import, timeout=WAIT_MS)
    files = []
    for number in (1, 2):
        path = tmp_path / f"drop{number}.fit"
        path.write_bytes(fit_run(number))
        files.append(path)
    mime = mime_of(files)
    entering = drag_enter_event(mime)
    window.dragEnterEvent(entering)
    assert entering.isAccepted()

    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.dropEvent(
            QDropEvent(
                QPointF(5, 5),
                Qt.DropAction.CopyAction,
                mime,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    report = blocker.args[0] if blocker.args else None
    assert isinstance(report, ImportReport)
    assert report.imported == 2
    wait_ready(qtbot, window, 2)
    assert settings.value("import/lastFilesFolder", "", type=str) == str(tmp_path)


# --- Barra laterale, menu, fuso, riga di comando ----------------------------------------------


def test_sidebar_has_overview_and_activities_enabled_and_the_rest_with_a_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None
    items = [sidebar.item(row) for row in range(sidebar.count())]
    assert [item.text() for item in items][:2] == ["Overview", "Activities"]
    for item in items[:2]:
        assert item.flags() & item.flags().ItemIsEnabled
    for item in items[2:]:
        assert not item.flags() & item.flags().ItemIsEnabled
        assert item.toolTip() == "Available in a later version"


def test_import_commands_are_disabled_with_the_reason_until_a_profile_is_open(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert not window.import_files_action.isEnabled()
    assert not window.import_folder_action.isEnabled()
    assert window.import_files_action.toolTip() == "Create or open a profile first."
    assert not window.stop_action.isEnabled()
    assert window.stop_action.toolTip()


def test_menus_carry_the_shortcuts(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    shortcuts = {
        action.text(): action.shortcut().toString()
        for action in window.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    assert shortcuts == {
        "New profile...": QKeySequence("Ctrl+N").toString(),
        "Open profile from folder...": QKeySequence("Ctrl+O").toString(),
        "Import files...": QKeySequence("Ctrl+I").toString(),
        "Import from folder...": QKeySequence("Ctrl+Shift+I").toString(),
        "Stop": QKeySequence("Esc").toString(),
        "Quit": QKeySequence("Ctrl+Q").toString(),
        "Open the log folder": QKeySequence("Ctrl+Shift+L").toString(),
        "Connect Garmin account...": QKeySequence("Ctrl+G").toString(),
        "Disconnect Garmin account": QKeySequence("Ctrl+Shift+G").toString(),
        "Synchronise": QKeySequence("F5").toString(),
        "Full check": QKeySequence("Ctrl+F5").toString(),
        "Stop synchronisation": QKeySequence("Shift+F5").toString(),
        "Synchronise when a profile opens": QKeySequence("Ctrl+Shift+A").toString(),
    }


def test_window_has_no_status_labels_filled_before_a_profile_is_open(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    assert all(label.isHidden() for label in window.findChildren(QLabel) if "(" in label.text())


@pytest.mark.parametrize(
    ("zone_id", "expected_zone", "fell_back"),
    [("Europe/Rome", "Europe/Rome", False), ("Not/AZone", "UTC", True), ("", "UTC", True)],
)
def test_user_time_zone_falls_back_to_utc(
    zone_id: str, expected_zone: str, fell_back: bool
) -> None:
    zone, did_fall_back = resolve_user_timezone(zone_id)

    assert zone.key == expected_zone
    assert did_fall_back is fell_back


def test_command_line_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("ponos-coach ")
    assert main(["--no-such-option"]) == 2
    assert "usage:" in capsys.readouterr().err
    assert main(["--fit-folder", "x"]) == 2  # l'opzione provvisoria non esiste più
