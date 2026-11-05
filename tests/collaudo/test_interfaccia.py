# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-01, T-UI-02, T-UI-07, T-SEC-08 e D-F1-02 dall'avvio vero (Composition Root + Qt offscreen).

Si parte da `compose_main_window` come fa `main()`: stesse porte reali, stessa finestra. Qui sono
i percorsi della fase 1 che i test della finestra non coprono: un albero ostile importato dalla
finestra, due profili che restano separati, un database sostituito da un file qualunque, la
cartella e le impostazioni ricordate davvero su disco, i log dell'intero percorso.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog, QListWidget, QMessageBox
from pytestqt.qtbot import QtBot

from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from tests.collaudo.support import format_logs
from tests.collaudo.test_import_pila_reale import mixed_tree
from tests.fixtures.generators import gpx_writer as gw
from tests.fixtures.generators import import_files as imp
from tests.ui.workspace_support import (
    WAIT_MS,
    compose,
    create_profile,
    make_settings,
    wait_ready,
)

SECRET_NAME = "Casa_Segreta_Via_Roma"  # noqa: S105 - nome inventato, non una password
SECRET_LAT, SECRET_LON = 41.987654, 12.345678  # coordinate inventate, riconoscibili nei log


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def start(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    return window


def import_and_wait(qtbot: QtBot, window: MainWindow, folder: Path) -> ImportReport:
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS) as blocker:
        window.import_folder(folder)
    report = blocker.args[0] if blocker.args else None
    assert isinstance(report, ImportReport)
    return report


def restart(qtbot: QtBot, window: MainWindow, tmp_path: Path, settings: QSettings) -> MainWindow:
    window.close()  # chiude il profilo nel thread dei dati e rilascia il lock
    return start(qtbot, tmp_path, settings)


def starts_recorder(seen: list[str]) -> Callable[..., str]:
    """Al posto di `getExistingDirectory`: ricorda la cartella di partenza e annulla."""

    def record(_parent: object, _caption: str, directory: str = "") -> str:
        seen.append(directory)
        return ""

    return record


def activity_rows(window: MainWindow) -> int:
    return window.activities_page.model.rowCount()


def open_error_box(qtbot: QtBot, window: MainWindow) -> QMessageBox:
    def find() -> QMessageBox | None:
        return next(
            (
                box
                for box in window.open_boxes
                if isinstance(box, QMessageBox) and box.objectName() == "errorMessage"
            ),
            None,
        )

    qtbot.waitUntil(lambda: find() is not None, timeout=WAIT_MS)
    box = find()
    assert box is not None
    return box


# --- T-UI-01 -------------------------------------------------------------------------------------


def test_t_ui_01_a_hostile_tree_from_the_window_then_again_then_a_new_session(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    source = tmp_path / "sorgente"
    mixed_tree(source)

    first = import_and_wait(qtbot, window, source)

    assert (first.imported, first.already_present, first.ignored, first.failed) == (4, 2, 1, 7)
    assert not first.cancelled
    assert len(first.not_imported) == 8  # ogni file rifiutato o ignorato ha il suo motivo
    wait_ready(qtbot, window, 4)
    assert activity_rows(window) == 4

    again = import_and_wait(qtbot, window, source)

    assert (again.imported, again.already_present) == (0, 6)
    assert len(again.not_imported) == 8  # i rifiuti restano rifiuti, ogni volta
    wait_ready(qtbot, window, 4)

    second_session = restart(qtbot, window, tmp_path, settings)
    qtbot.waitUntil(lambda: second_session.controller.profile is not None, timeout=WAIT_MS)
    wait_ready(qtbot, second_session, 4)
    assert second_session.windowTitle() == "Atleta di prova - Ponos Coach"
    assert activity_rows(second_session) == 4


def test_t_ui_01_two_profiles_stay_separate_and_the_last_one_used_reopens(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Prima atleta")
    first_source = tmp_path / "uno"
    first_source.mkdir()
    for number in range(3):
        (first_source / f"{number}.fit").write_bytes(imp.fit_run(number))
    import_and_wait(qtbot, window, first_source)
    wait_ready(qtbot, window, 3)
    create_profile(qtbot, window, "Seconda atleta")
    assert activity_rows(window) == 0  # il profilo nuovo non eredita nulla
    second_source = tmp_path / "due"
    second_source.mkdir()
    (second_source / "a.gpx").write_bytes(imp.gpx_run(50))
    import_and_wait(qtbot, window, second_source)
    wait_ready(qtbot, window, 1)
    first_id = next(
        e.profile_id for e in window.controller.profiles if "Prima" in e.cached_name.value
    )

    window.controller.switch_profile(first_id)

    qtbot.waitUntil(
        lambda: (
            window.controller.profile is not None
            and window.controller.profile.profile_id == first_id
            and isinstance(window.controller.overview_state, Ready)
        ),
        timeout=WAIT_MS,
    )
    wait_ready(qtbot, window, 3)
    assert activity_rows(window) == 3
    reopened = restart(qtbot, window, tmp_path, settings)
    qtbot.waitUntil(lambda: reopened.controller.profile is not None, timeout=WAIT_MS)
    assert reopened.controller.profile is not None
    assert reopened.controller.profile.profile_id == first_id  # l'ultimo usato, non l'ultimo creato
    wait_ready(qtbot, reopened, 3)


def test_t_ui_01_a_database_replaced_by_a_foreign_file_gives_a_message_and_a_usable_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    profile_folder = tmp_path / "profiles" / "Atleta di prova"
    window.close()
    database = profile_folder / PROFILE_DATABASE_NAME
    original = database.read_bytes()
    garbage = b"un altro programma ha scritto qui " * 100
    database.write_bytes(garbage)

    again = start(qtbot, tmp_path, settings)

    box = open_error_box(qtbot, again)
    assert "Atleta" not in box.informativeText()  # nessun nome ne percorso nel messaggio
    assert str(tmp_path) not in box.informativeText() + box.text()
    assert again.controller.profile is None
    assert again.new_profile_action.isEnabled()  # la finestra resta usabile
    assert database.read_bytes() == garbage  # e il file non e stato toccato
    again.close()
    database.write_bytes(original)
    recovered = start(qtbot, tmp_path, settings)
    qtbot.waitUntil(lambda: recovered.controller.profile is not None, timeout=WAIT_MS)


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-02: aprendo il profilo i temporanei orfani lasciati da un'interruzione non "
    "si cancellano mai (remove_orphan_temporaries non e chiamato da nessuno)",
)
def test_d_f1_02_opening_a_profile_removes_the_orphan_temporaries_of_an_interrupted_write(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    window.close()
    year_folder = tmp_path / "profiles" / "Atleta di prova" / "archivio" / "2026"
    year_folder.mkdir(parents=True)
    orphan = year_folder / (".tmp-" + "0123456789abcdef" * 2)
    orphan.write_bytes(b"scrittura interrotta")

    again = start(qtbot, tmp_path, settings)
    qtbot.waitUntil(lambda: again.controller.profile is not None, timeout=WAIT_MS)
    again.close()  # chiude il profilo: tutto cio che l'apertura doveva fare e fatto

    assert not orphan.exists()


# --- T-UI-07 -------------------------------------------------------------------------------------


def test_t_ui_07_folders_are_on_disk_for_a_new_process_and_offered_by_the_dialogs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    source = tmp_path / "ultima cartella"
    source.mkdir()
    (source / "a.fit").write_bytes(imp.fit_run(1))
    import_and_wait(qtbot, window, source)
    files_folder = tmp_path / "ultimi file"
    files_folder.mkdir()
    (files_folder / "b.fit").write_bytes(imp.fit_run(2))
    window.import_files([files_folder / "b.fit"])
    qtbot.waitUntil(lambda: window.controller.can_import, timeout=WAIT_MS)
    window.close()

    other_process = QSettings(settings.fileName(), QSettings.Format.IniFormat)  # come al riavvio
    assert other_process.value("import/lastFolder", "", type=str) == str(source)
    assert other_process.value("import/lastFilesFolder", "", type=str) == str(files_folder)

    seen: dict[str, str] = {}

    def fake_folder(parent: object, caption: str, directory: str = "") -> str:
        seen["folder"] = directory
        return ""

    def fake_files(
        parent: object, caption: str, directory: str = "", filter_: str = ""
    ) -> tuple[list[str], str]:
        seen["files"] = directory
        return [], ""

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(fake_folder))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", staticmethod(fake_files))
    restarted = compose(tmp_path, QSettings(settings.fileName(), QSettings.Format.IniFormat))
    qtbot.addWidget(restarted)
    restarted.start()
    qtbot.waitUntil(lambda: restarted.controller.profile is not None, timeout=WAIT_MS)

    restarted.choose_import_folder()
    restarted.choose_import_files()

    assert seen == {"folder": str(source), "files": str(files_folder)}


def test_t_ui_07_the_profile_folder_dialog_starts_where_the_last_one_was_chosen(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    elsewhere = tmp_path / "altrove" / "profilo-vecchio"
    window.open_profile_folder(elsewhere)  # non esiste: un errore, ma la cartella si ricorda
    seen: list[str] = []
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(starts_recorder(seen)),
    )

    window.choose_profile_folder()

    assert seen == [str(elsewhere.parent)]


def test_t_ui_07_a_binary_geometry_that_cannot_be_restored_gives_the_default_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    settings.setValue("window/geometry", b"\x00\x01\x02")
    settings.sync()

    window = start(qtbot, tmp_path, settings)

    assert (window.width(), window.height()) == (1100, 720)


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-09: se il valore salvato per la geometria non e un blocco di byte (impostazioni "
    "modificate a mano o rovinate) restoreGeometry solleva TypeError e l'app non parte",
)
@pytest.mark.parametrize("garbage", ["testo", 42], ids=["testo", "numero"])
def test_d_f1_09_a_geometry_of_the_wrong_type_gives_the_default_window_not_a_crash(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, garbage: object
) -> None:
    settings.setValue("window/geometry", garbage)
    settings.sync()

    window = start(qtbot, tmp_path, settings)

    assert (window.width(), window.height()) == (1100, 720)

    window = start(qtbot, tmp_path, settings)

    assert (window.width(), window.height()) == (1100, 720)


def test_t_ui_07_settings_with_wrong_types_for_the_folders_do_not_break_the_dialogs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key in ("import/lastFolder", "import/lastFilesFolder", "profiles/lastOpenFolder"):
        settings.setValue(key, 12345)
    window = start(qtbot, tmp_path, settings)
    seen: list[str] = []
    monkeypatch.setattr(
        QFileDialog,
        "getExistingDirectory",
        staticmethod(starts_recorder(seen)),
    )

    window.choose_profile_folder()
    window.choose_import_folder()

    assert len(seen) == 2


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-08: T-UI-07 dice che l'app ricorda i pannelli, ma la sezione aperta nella "
    "barra laterale (Panoramica o Attivita) non si salva",
)
def test_d_f1_08_the_open_section_is_remembered_at_the_next_start(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None
    sidebar.setCurrentRow(1)  # Attivita
    window.close()

    again = start(qtbot, tmp_path, settings)
    qtbot.waitUntil(lambda: again.controller.profile is not None, timeout=WAIT_MS)

    again_sidebar = again.findChild(QListWidget, "sidebar")
    assert again_sidebar is not None
    assert again_sidebar.currentRow() == 1


# --- T-SEC-08 ------------------------------------------------------------------------------------


def secret_gpx() -> bytes:
    points = gw.line_points(
        imp.run_start(1),
        61,
        step_s=10,
        lat_step_deg=0.0001,
        first_lat=SECRET_LAT,
        first_lon=SECRET_LON,
        hr=[187] * 61,
    )
    return gw.build_gpx(gw.GpxSpec([points], name=SECRET_NAME))


def assert_log_is_clean(text: str, tmp_path: Path) -> None:
    forbidden = [
        SECRET_NAME,
        "41.9876",
        "12.3456",
        str(tmp_path),
        tmp_path.name,
        Path.home().name,
        "Atleta di prova",
    ]
    for item in forbidden:
        assert item not in text, f"il log contiene {item!r}"


@pytest.fixture
def all_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG)
    return caplog


def test_t_sec_08_import_open_and_overview_log_no_data_of_the_user(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, all_logs: pytest.LogCaptureFixture
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    source = tmp_path / SECRET_NAME
    mixed_tree(source)
    (source / f"{SECRET_NAME}.gpx").write_bytes(secret_gpx())
    (source / f"{SECRET_NAME}_rotto.fit").write_bytes(b"non un fit " + SECRET_NAME.encode())
    import_and_wait(qtbot, window, source)
    wait_ready(qtbot, window, 5)
    reopened = restart(qtbot, window, tmp_path, settings)
    qtbot.waitUntil(lambda: reopened.controller.profile is not None, timeout=WAIT_MS)
    wait_ready(qtbot, reopened, 5)
    reopened.close()

    assert_log_is_clean(format_logs(all_logs.records), tmp_path)


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-05: un lavoro fallito scrive nel log il traceback con il testo delle "
    "eccezioni, che contiene il nome della cartella scelta dall'utente",
)
@pytest.mark.parametrize("operation", ["open_missing", "import_missing"])
def test_d_f1_05_failures_do_not_write_the_folder_names_of_the_user_in_the_log(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    all_logs: pytest.LogCaptureFixture,
    operation: str,
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    secret_folder = tmp_path / SECRET_NAME
    if operation == "open_missing":
        window.open_profile_folder(secret_folder)  # non esiste
    else:
        window.import_folder(secret_folder)  # non esiste
    open_error_box(qtbot, window)

    assert_log_is_clean(format_logs(all_logs.records), tmp_path)


@pytest.mark.xfail(
    strict=True,
    reason="D-F1-05: il nome di una cartella con un a-capo arriva intero nel log e puo "
    "fingere una riga di log (i caratteri di controllo non sono neutralizzati)",
)
def test_d_f1_05_control_characters_from_user_input_cannot_forge_a_log_line(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, all_logs: pytest.LogCaptureFixture
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    hostile = tmp_path / "x\nERROR ponos_coach.fake riga falsa"
    window.open_profile_folder(hostile)
    open_error_box(qtbot, window)

    lines = format_logs(all_logs.records).splitlines()

    assert not [line for line in lines if line.startswith("ERROR ponos_coach.fake")]


def test_t_sec_08_the_error_message_shown_to_the_user_never_repeats_the_input(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = start(qtbot, tmp_path, settings)
    create_profile(qtbot, window, "Atleta di prova")
    window.import_folder(tmp_path / SECRET_NAME)  # non esiste

    box = open_error_box(qtbot, window)

    shown = box.text() + box.informativeText()
    assert SECRET_NAME not in shown
    assert str(tmp_path) not in shown
    assert re.search(r"[\x00-\x08\x0b-\x1f]", shown) is None
