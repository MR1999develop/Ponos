# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fase 3: lingua, nuovo profilo con i dati del corpo, modifica profilo e pesi degli sport.

Si parte dalla finestra composta come fa `main()`, con profili e dati in `tmp_path`. Tutti i
valori sono inventati (nessuna persona reale).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QLocale, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QMenu, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    Sex,
    ThresholdHeartRate,
    default_weight,
)
from ponos_coach.profiles.domain import ProfileName, Weight
from ponos_coach.profiles.measurements import MeasurementUpdate
from ponos_coach.profiles.use_cases import NewProfileRequest
from ponos_coach.ui.birth_date_edit import NOT_SET_DATE
from ponos_coach.ui.body_input import latest_birth_date
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.measurements_form import EditField, MeasurementsRequest
from ponos_coach.ui.new_profile_dialog import NewProfileDialog
from ponos_coach.ui.new_profile_form import Field
from ponos_coach.ui.overview_state import Loading, Ready
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.sport_weights_dialog import SportWeightsDialog
from ponos_coach.ui.translations import Language, saved_language
from tests.ui.test_main_window import open_message_box
from tests.ui.workspace_support import (
    FILES_IN_FIXTURE_FOLDER,
    TODAY,
    WAIT_MS,
    close_window,
    compose,
    create_profile,
    fill_body_data,
    make_import_folder,
    make_settings,
    pick_profile_folder,
    wait_ready,
)

PROFILE = "Atleta di prova"


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    """Numeri e date nel formato inglese, qualunque sia la lingua del computer che prova."""
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def window_with_activities(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    """Profilo creato dall'interfaccia, con le otto attività della cartella di prova."""
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    create_profile(qtbot, window, PROFILE)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    return window


def first_load(window: MainWindow) -> float:
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    return state.overview.activities[0].load.load


def open_edit_dialog(qtbot: QtBot, window: MainWindow) -> ProfileEditDialog:
    qtbot.waitUntil(window.edit_profile_action.isEnabled, timeout=WAIT_MS)
    window.edit_profile_action.trigger()
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is not None, timeout=WAIT_MS)
    dialog = window.profile_dialogs.edit_dialog
    assert dialog is not None
    return dialog


def open_weights_dialog(qtbot: QtBot, window: MainWindow) -> SportWeightsDialog:
    qtbot.waitUntil(window.sport_weights_action.isEnabled, timeout=WAIT_MS)
    window.sport_weights_action.trigger()
    qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is not None, timeout=WAIT_MS)
    dialog = window.profile_dialogs.weights_dialog
    assert dialog is not None
    return dialog


def chosen_birth(dialog: NewProfileDialog) -> date | None:
    """La data di nascita nel modulo, letta da una funzione (il tipo non si restringe)."""
    return dialog.form.birth_date


def click(button: QWidget) -> None:
    QTest.mouseClick(button, Qt.MouseButton.LeftButton)


# --- Lingua -----------------------------------------------------------------------------------


def test_the_language_menu_offers_english_and_italian_with_english_checked_by_default(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    titles = [action.text() for action in window.menuBar().actions()]
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Language")
    assert isinstance(menu, QMenu)

    assert titles == ["File", "Profile", "Garmin", "Language", "Help"]
    assert [a.text() for a in menu.actions()] == ["English", "Italiano"]
    actions = window.language_actions
    assert actions[Language.ENGLISH].isChecked()
    assert not actions[Language.ITALIAN].isChecked()


def test_choosing_italian_is_remembered_and_the_window_says_when_it_applies(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.language_actions[Language.ITALIAN].trigger()

    box = open_message_box(qtbot, window, "languageChanged")
    assert box.text() == "The language changes the next time Ponos Coach starts."
    assert saved_language(settings) is Language.ITALIAN
    assert window.windowTitle() == "Ponos Coach"  # non cambia a caldo
    box.close()

    chosen = saved_language(settings) or Language.ENGLISH
    restarted = compose(tmp_path / "second", settings, language=chosen)
    qtbot.addWidget(restarted)
    assert restarted.language_actions[Language.ITALIAN].isChecked()


def test_choosing_the_active_language_again_shows_no_message(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)

    window.language_actions[Language.ENGLISH].trigger()

    assert not [b for b in window.open_boxes if b.objectName() == "languageChanged"]
    assert saved_language(settings) is Language.ENGLISH


# --- Nuovo profilo con altezza, peso e data di nascita ---------------------------------


def open_new_dialog(qtbot: QtBot, window: MainWindow) -> NewProfileDialog:
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    return dialog


def test_creation_asks_for_the_body_data_and_shows_the_first_error_on_submit(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_new_dialog(qtbot, window)
    QTest.keyClicks(dialog.name_edit, PROFILE)
    pick_profile_folder(window, dialog, tmp_path / "profiles" / PROFILE)
    assert dialog.height_edit.text() == ""  # nessun dato inventato
    assert dialog.weight_edit.text() == ""
    assert dialog.birth_edit.selected_date() is None
    assert dialog.error_text(Field.HEIGHT) == ""  # mai errori alla prima scrittura

    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)

    assert dialog.error_text(Field.HEIGHT) == "Enter the height in centimetres."
    assert dialog.error_text(Field.WEIGHT) == "Enter the weight in kilograms."
    assert dialog.error_text(Field.BIRTH_DATE) == "Enter the date of birth."
    assert window.controller.profile is None
    assert window.new_profile_dialog is dialog  # resta aperto con i dati scritti


def test_body_errors_update_while_typing_and_never_repeat_the_typed_value(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_new_dialog(qtbot, window)
    pick_profile_folder(window, dialog, tmp_path / "profiles" / PROFILE)
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)  # attiva gli errori
    QTest.keyClicks(dialog.height_edit, "31337")
    QTest.keyClicks(dialog.weight_edit, "66,66")

    height = dialog.error_text(Field.HEIGHT)
    weight = dialog.error_text(Field.WEIGHT)

    assert height == "Enter the height as a whole number of centimetres, from 100 to 250."
    assert weight == "Enter the weight in kilograms with at most one decimal, from 20 to 300."
    assert "31337" not in height
    assert "66" not in weight
    dialog.height_edit.clear()
    QTest.keyClicks(dialog.height_edit, "180")
    assert dialog.error_text(Field.HEIGHT) == ""


def test_the_birth_date_control_has_the_limits_of_the_domain(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_new_dialog(qtbot, window)
    edit = dialog.birth_edit

    latest = latest_birth_date(TODAY)

    assert edit.maximumDate() == QDate(latest.year, latest.month, latest.day)
    assert edit.minimumDate() == NOT_SET_DATE  # un giorno prima del 1900-01-01
    assert edit.minimumDate().addDays(1) == QDate(1900, 1, 1)
    edit.select(latest)  # on point
    assert chosen_birth(dialog) == latest
    edit.setDate(edit.maximumDate().addDays(1))  # off point: il controllo lo riporta al limite
    assert chosen_birth(dialog) == latest
    edit.select(None)
    assert chosen_birth(dialog) is None
    assert edit.specialValueText() == "Select the date"


def test_the_stored_body_data_come_back_in_edit_profile_and_the_weight_in_the_history(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    dialog = open_new_dialog(qtbot, window)
    QTest.keyClicks(dialog.name_edit, PROFILE)
    fill_body_data(dialog, "183", "71.3", date(1987, 6, 9))
    pick_profile_folder(window, dialog, tmp_path / "profiles" / PROFILE)
    QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.controller.profile is not None, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    edit = open_edit_dialog(qtbot, window)

    labels = {label.objectName(): label.text() for label in edit.findChildren(QLabel)}
    assert labels["fixedName"] == PROFILE
    assert labels["fixedHeightValue"] == "183 cm"
    assert labels["fixedSex"] == "Male"
    assert edit.height_edit.isHidden()  # niente da completare
    assert edit.birth_edit.isHidden()
    model = edit.history_table.model()
    assert model.rowCount() == 1
    assert model.data(model.index(0, 1)) == "71.3"
    assert model.data(model.index(0, 4)) == "171"  # soglia stimata al 90% della massima di 190


def test_body_data_never_reach_the_logs(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        window = compose(tmp_path, settings)
        qtbot.addWidget(window)
        dialog = open_new_dialog(qtbot, window)
        QTest.keyClicks(dialog.name_edit, PROFILE)
        fill_body_data(dialog, "183", "71.3", date(1987, 6, 9))
        pick_profile_folder(window, dialog, tmp_path / "profiles" / PROFILE)
        QTest.mouseClick(dialog.create_button, Qt.MouseButton.LeftButton)
        qtbot.waitUntil(
            lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS
        )
        edit = open_edit_dialog(qtbot, window)
        edit.weight_edit.clear()
        QTest.keyClicks(edit.weight_edit, "72,4")
        click(edit.save_button)
        qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)

    text = "\n".join(record.getMessage() for record in caplog.records)
    for secret in ("183", "71.3", "71,3", "72,4", "1987", "06-09"):
        assert secret not in text


# --- Modifica profilo ------------------------------------------------------------------


def test_a_past_measurement_changes_the_load_and_the_overview_is_recalculated(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    before = first_load(window)
    dialog = open_edit_dialog(qtbot, window)
    assert dialog.valid_from_edit.date() == QDate(2026, 6, 30)  # oggi per default
    assert dialog.valid_from_edit.maximumDate() == QDate(2026, 6, 30)  # mai nel futuro
    assert not dialog.save_button.isEnabled()
    assert dialog.save_button.toolTip() == "Change at least one value first."

    dialog.valid_from_edit.setDate(QDate(2026, 5, 1))
    dialog.threshold_box.setValue(150)
    assert dialog.save_button.isEnabled()
    click(dialog.save_button)

    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready) and first_load(window) != before
        ),
        timeout=WAIT_MS,
    )
    assert first_load(window) > before  # soglia più bassa: stessa FC pesa di più
    assert "recalculated" in window.statusBar().currentMessage()


def test_only_the_changed_fields_are_stored_and_the_history_lists_the_new_row(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    dialog = open_edit_dialog(qtbot, window)
    dialog.valid_from_edit.setDate(QDate(2026, 5, 1))
    dialog.threshold_box.setValue(150)
    click(dialog.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    again = open_edit_dialog(qtbot, window)

    model = again.history_table.model()
    assert model.rowCount() == 2
    rows = [
        [model.data(model.index(row, col)) for col in range(model.columnCount())]
        for row in range(model.rowCount())
    ]
    # la nuova voce tiene FC minima e massima di prima: cambia solo la soglia
    thresholds = {row[4] for row in rows}
    assert thresholds == {"171", "150"}
    assert {row[2] for row in rows} == {"50"}
    assert {row[3] for row in rows} == {"190"}


def test_a_weight_with_a_past_date_is_added_to_the_history(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    dialog = open_edit_dialog(qtbot, window)
    dialog.valid_from_edit.setDate(QDate(2026, 5, 10))
    dialog.weight_edit.clear()
    QTest.keyClicks(dialog.weight_edit, "64,0")
    click(dialog.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)

    again = open_edit_dialog(qtbot, window)

    model = again.history_table.model()
    assert model.rowCount() == 2  # la creazione e il 10 maggio
    weights = {model.data(model.index(row, 1)) for row in range(model.rowCount())}
    assert weights == {"65.5", "64.0"}


def test_an_invalid_weight_is_reported_on_the_field_and_nothing_is_saved(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    dialog = open_edit_dialog(qtbot, window)
    dialog.weight_edit.clear()
    QTest.keyClicks(dialog.weight_edit, "70,55")

    click(dialog.save_button)

    assert "at most one decimal" in dialog.error_text(EditField.WEIGHT)
    assert "70" not in dialog.error_text(EditField.WEIGHT).replace("300", "")
    assert window.profile_dialogs.edit_dialog is dialog  # resta aperto con i dati scritti
    assert dialog.weight_edit.text() == "70,55"


def test_heart_rate_that_does_not_fit_together_is_reported_before_saving(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    dialog = open_edit_dialog(qtbot, window)
    dialog.min_box.setValue(200)  # sopra la soglia e la massima

    click(dialog.save_button)

    assert dialog.error_text(EditField.MIN_BPM) == (
        "The resting heart rate must be lower than the maximum."
    )
    assert dialog.error_text(EditField.THRESHOLD)
    assert window.profile_dialogs.edit_dialog is dialog


def test_a_rejection_from_the_use_case_stays_open_with_the_message_on_the_right_field(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    dialog = open_edit_dialog(qtbot, window)
    future = MeasurementUpdate(date(2027, 1, 1), weight=Weight(700))

    dialog.save_requested.emit(MeasurementsRequest(future, None))  # il modulo non lo permette

    qtbot.waitUntil(lambda: dialog.error_text(EditField.VALID_FROM) != "", timeout=WAIT_MS)
    assert dialog.error_text(EditField.VALID_FROM) == "The date cannot be in the future."
    assert window.profile_dialogs.edit_dialog is dialog


def test_a_profile_created_without_height_and_birth_date_completes_them_once(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    window.start()
    qtbot.waitUntil(lambda: window.new_profile_dialog is not None, timeout=WAIT_MS)
    dialog = window.new_profile_dialog
    assert dialog is not None
    dialog.reject()
    request = NewProfileRequest(
        ProfileName.parse(PROFILE),
        tmp_path / "profiles" / PROFILE,
        "Europe/Rome",
        Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(171), Sex.FEMALE),
        TODAY,
    )  # come un profilo di una versione precedente: senza altezza, data di nascita e peso
    assert window.controller.create_profile(request)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    first = open_edit_dialog(qtbot, window)

    assert first.form.needs_fixed_data
    assert not first.height_edit.isHidden()
    assert first.history_table.model().rowCount() == 1  # solo la FC: nessun peso
    assert not first.save_button.isEnabled()
    QTest.keyClicks(first.height_edit, "168")
    click(first.save_button)  # manca la data di nascita
    assert first.error_text(EditField.BIRTH_DATE) == "Enter the date of birth."
    first.birth_edit.select(date(1991, 11, 5))
    click(first.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.edit_dialog is None, timeout=WAIT_MS)

    second = open_edit_dialog(qtbot, window)

    assert not second.form.needs_fixed_data
    assert second.height_edit.isHidden()
    labels = {label.objectName(): label.text() for label in second.findChildren(QLabel)}
    assert labels["fixedHeightValue"] == "168 cm"
    assert labels["fixedSex"] == "Female"


def test_edit_commands_wait_for_a_profile_and_for_the_calculation_with_the_reason(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    gate = threading.Event()
    window = compose(tmp_path, settings, gate=gate)
    qtbot.addWidget(window)
    for action in (window.edit_profile_action, window.sport_weights_action):
        assert not action.isEnabled()
        assert action.toolTip() == "Create or open a profile first."
    window.start()
    create_profile(qtbot, window, PROFILE)
    with qtbot.waitSignal(window.controller.import_finished, timeout=WAIT_MS):
        window.import_folder(make_import_folder(tmp_path))

    qtbot.waitUntil(lambda: window.controller.overview_state == Loading(0, 8), timeout=WAIT_MS)

    for action in (window.edit_profile_action, window.sport_weights_action):
        assert not action.isEnabled()
        assert action.toolTip() == "Wait for the current operation to finish, or stop it first."
    gate.set()
    wait_ready(qtbot, window, FILES_IN_FIXTURE_FOLDER)
    qtbot.waitUntil(window.edit_profile_action.isEnabled, timeout=WAIT_MS)
    assert window.sport_weights_action.isEnabled()
    assert "Wait" not in window.edit_profile_action.toolTip()


# --- Pesi degli sport -------------------------------------------------------------------------


def test_sport_weights_dialog_shows_the_defaults_and_explains_the_effect(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)

    dialog = open_weights_dialog(qtbot, window)

    for sport in Sport:
        box = dialog.box(sport)
        assert box.minimum() == 0.0
        assert box.maximum() == 5.0
        assert box.decimals() == 2
        assert box.value() == default_weight(sport).value
    explanation = next(
        label.text() for label in dialog.findChildren(QLabel) if "multiplies" in label.text()
    )
    assert "recalculates every activity" in explanation
    assert not dialog.save_button.isEnabled()
    assert not dialog.restore_button.isEnabled()  # già i predefiniti
    assert dialog.restore_button.toolTip() == "All weights already have their default value."


def test_saving_a_sport_weight_rescales_the_load_and_restoring_defaults_brings_it_back(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    before = first_load(window)
    dialog = open_weights_dialog(qtbot, window)
    dialog.box(Sport.RUNNING).setValue(2.0)
    assert dialog.save_button.isEnabled()
    assert dialog.restore_button.isEnabled()
    click(dialog.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is None, timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready) and first_load(window) != before
        ),
        timeout=WAIT_MS,
    )
    assert first_load(window) == pytest.approx(before * 2.0)

    again = open_weights_dialog(qtbot, window)
    assert again.box(Sport.RUNNING).value() == 2.0  # persistito
    click(again.restore_button)
    assert again.box(Sport.RUNNING).value() == default_weight(Sport.RUNNING).value
    click(again.save_button)
    qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is None, timeout=WAIT_MS)
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and first_load(window) == pytest.approx(before)
        ),
        timeout=WAIT_MS,
    )


def test_cancel_leaves_the_weights_unchanged(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    before = first_load(window)
    dialog = open_weights_dialog(qtbot, window)
    dialog.box(Sport.RUNNING).setValue(3.0)

    dialog.reject()

    assert window.profile_dialogs.weights_dialog is None or not dialog.isVisible()
    assert first_load(window) == before
    reopened = open_weights_dialog(qtbot, window)
    assert reopened.box(Sport.RUNNING).value() == default_weight(Sport.RUNNING).value


def test_closing_the_window_with_the_dialogs_open_finishes_the_closing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    open_weights_dialog(qtbot, window)

    close_window(qtbot, window)

    assert window.is_closed
    assert window.profile_dialogs.weights_dialog is None
