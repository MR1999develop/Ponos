# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-08 per i dati personali della fase 3: altezza, peso, data di nascita e FC mai nei log.

Si percorre l'interfaccia vera con valori inventati e riconoscibili (nessuna persona reale):
creazione del profilo, modifica delle misure, pesi degli sport, input rifiutati, un guasto del
database e un'eccezione imprevista il cui testo contiene i valori. Poi si cerca ogni valore in tutto
ciò che l'app scrive o mostra: il file di log, i record catturati (messaggio, argomenti, traceback)
e i testi dei messaggi e dei campi d'errore.
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

from ponos_coach.activities.domain import Sport
from ponos_coach.profiles.ports import ProfileDataError
from ponos_coach.storage import sqlite_profile_data
from ponos_coach.ui.diagnostics import LOG_FILE_NAME, LOGGER_NAME, SafeFormatter, configure_logging
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.measurements_form import EditField
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from tests.collaudo.support import format_logs
from tests.ui.test_profile_data_window import (
    PROFILE,
    click,
    open_edit_dialog,
    open_weights_dialog,
)
from tests.ui.workspace_support import (
    WAIT_MS,
    close_window,
    compose,
    fill_body_data,
    make_settings,
    pick_profile_folder,
)

HEIGHT = "187"
WEIGHT_TYPED = "93,7"
BIRTH = date(1983, 11, 27)
FIRST_WEIGHT_UPDATE = "88,3"
MIN_BPM, MAX_BPM, THRESHOLD_BPM = 43, 199, 181
LEAKY = "the weight 77.7 kg and height 191 cm"  # il testo di un'eccezione che contiene dati

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
    """Il file di log come lo configura `main()`, tolto a fine test."""
    path = configure_logging(tmp_path / "logs", version="collaudo")
    assert path is not None
    yield path
    logger = logging.getLogger(LOGGER_NAME)
    for handler in [h for h in logger.handlers if getattr(h, "_ponos_coach_handler", False)]:
        logger.removeHandler(handler)
        handler.close()


def strip_timestamps(text: str) -> str:
    """Toglie la data e l'ora iniziali di ogni riga del file di log (cifre dell'orologio)."""
    return "\n".join(re.sub(r"^\S+T\S+Z ", "", line) for line in text.splitlines())


def visible_boxes() -> list[QMessageBox]:
    """Ogni messaggio visibile dell'app, comunque sia stato aperto."""
    return [
        w for w in QApplication.topLevelWidgets() if isinstance(w, QMessageBox) and w.isVisible()
    ]


def box_texts() -> str:
    parts: list[str] = []
    for box in visible_boxes():
        parts += [box.text(), box.informativeText(), box.detailedText(), box.windowTitle()]
    return "\n".join(parts)


def error_texts(dialog: ProfileEditDialog) -> str:
    """I messaggi d'errore sotto i campi (non i dati che il dialogo mostra all'utente)."""
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
    """Modifiche valide, input rifiutati, guasti e pesi; dà i testi che l'utente ha visto."""
    shown: list[str] = []
    # 1. modifica valida delle misure
    edit = open_edit_dialog(qtbot, window)
    edit.weight_edit.clear()
    QTest.keyClicks(edit.weight_edit, FIRST_WEIGHT_UPDATE)
    click(edit.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)

    # 2. input rifiutati sul posto: testo non numerico, peso fuori limite, FC incoerenti
    edit = open_edit_dialog(qtbot, window)
    for text in ("abc", "19,9", "301", "93,77"):
        edit.weight_edit.clear()
        QTest.keyClicks(edit.weight_edit, text)
        click(edit.save_button)
        shown.append(error_texts(edit))
    edit.weight_edit.clear()
    edit.min_box.setValue(MAX_BPM)  # minima sopra la massima
    edit.threshold_box.setValue(THRESHOLD_BPM)
    click(edit.save_button)
    shown.append(error_texts(edit))

    # 3. guasto del database (errore noto del salvataggio) e poi eccezione imprevista con dati
    def known_failure(*_args: object) -> None:
        raise ProfileDataError("scrittura del peso non riuscita")

    def leaky_failure(*_args: object) -> None:
        raise RuntimeError(LEAKY)

    edit.reject()
    for failure in (known_failure, leaky_failure):
        monkeypatch.setattr(sqlite_profile_data.SqliteWeightStore, "put", failure)
        again = open_edit_dialog(qtbot, window)
        again.weight_edit.clear()
        QTest.keyClicks(again.weight_edit, "91,5")
        click(again.save_button)
        qtbot.waitUntil(lambda: bool(visible_boxes()), timeout=WAIT_MS)
        shown.append(box_texts())
        shown.append(error_texts(again))
        for box in visible_boxes():
            box.close()
        monkeypatch.undo()
        again.reject()

    # 4. pesi degli sport
    weights = open_weights_dialog(qtbot, window)
    weights.box(Sport.CYCLING).setValue(1.25)
    click(weights.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is None, timeout=WAIT_MS)
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
        "righe del formatter sicuro": safe_lines,
        "messaggi grezzi": raw_messages,
        "messaggi e traceback": raw_with_traceback,
        "testi mostrati": "\n".join(shown),
    }
    assert log_file.exists() and on_disk  # il file di log esiste e ha scritto qualcosa
    leaks = {
        (where, kind, match.group(0))
        for where, text in everywhere.items()
        for kind, pattern in FORBIDDEN.items()
        for match in pattern.finditer(text)
        # il traceback grezzo contiene per forza il testo dell'eccezione imprevista: è il
        # formatter sicuro (quello del file) a dover tacere, non il traceback di pytest
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
    click(edit.save_button)
    qtbot.waitUntil(lambda: bool(visible_boxes()), timeout=WAIT_MS)
    texts = box_texts()
    monkeypatch.undo()

    assert not FORBIDDEN["eccezione"].search(texts)
    assert "RuntimeError" not in texts  # nessun nome di classe all'utente
    assert texts.strip()  # ma un messaggio c'è
    for box in visible_boxes():
        box.close()
    edit.reject()
    close_window(qtbot, window)
    log = (tmp_path / "logs" / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "RuntimeError" in log  # il dettaglio per chi indaga: il tipo
    assert not FORBIDDEN["eccezione"].search(log)  # mai il testo
