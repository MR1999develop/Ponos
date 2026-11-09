# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Settings window: one window for the profile (with the Garmin account), the sport weights,
the training plan, the nutrition, the map and the language.

We start from the window composed as `main()` does. Every page keeps its own way of saving: the
tests of what each page saves are in the files of the data they change (profile, weights, plan,
nutrition, map); here are the window, the navigation, the unsaved edits and the old commands that
now open it. All values are made up (no real person).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMenu
from pytestqt.qtbot import QtBot

from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.map_settings_dialog import MapSettingsDialog
from ponos_coach.ui.measurements_form import EditField
from ponos_coach.ui.settings_page import SettingsCategory
from ponos_coach.ui.settings_window import SettingsWindow
from ponos_coach.ui.sync_model import AccountStatus
from ponos_coach.ui.threshold_protocol_section import ThresholdProtocolSection
from ponos_coach.ui.translations import Language, saved_language
from tests.ui.garmin_support import wait_account
from tests.ui.nutrition_support import DAWN_AND_LUNCH_DAY, open_day, window_with_days
from tests.ui.test_garmin_window import window_with_profile
from tests.ui.test_main_window import open_message_box
from tests.ui.test_profile_data_window import (
    click,
    open_edit_dialog,
    open_weights_dialog,
    window_with_activities,
)
from tests.ui.workspace_support import (
    WAIT_MS,
    answer_unsaved,
    close_window,
    compose,
    language_form,
    make_fakes,
    make_settings,
    wait_profile_saved,
)

NO_PROFILE = "Create or open a profile first."


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    """Numbers and dates in English format, whatever the language of the test computer."""
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def empty_window(qtbot: QtBot, tmp_path: Path, settings: QSettings) -> MainWindow:
    window = compose(tmp_path, settings)
    qtbot.addWidget(window)
    return window


def file_menu(window: MainWindow) -> QMenu:
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "File")
    assert isinstance(menu, QMenu)
    return menu


def settings_command(window: MainWindow) -> QAction:
    return next(a for a in file_menu(window).actions() if a.text() == "Settings...")


def current(window: MainWindow) -> SettingsCategory:
    """The category in view, read through a function (the type is not narrowed)."""
    return window.settings_window.current_category


def note_shown(window: MainWindow) -> bool:
    """Whether the Language page says the change applies at the next start."""
    return language_form(window).note_is_shown


def category_names(window: MainWindow) -> list[str]:
    return [window.settings_window.page(c).title for c in window.settings_window.categories]


# --- Opening -------------------------------------------------


def test_file_menu_has_settings_with_the_standard_shortcut_and_opens_one_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    command = settings_command(window)
    preferences = QKeySequence(QKeySequence.StandardKey.Preferences)
    expected = preferences if not preferences.isEmpty() else QKeySequence("Ctrl+,")
    assert command.shortcut() == expected
    assert not window.settings_window.isVisible()

    command.trigger()

    assert window.settings_window.isVisible()
    assert window.settings_window.windowTitle() == "Settings"


def test_opening_again_brings_the_same_window_forward_and_never_makes_a_second(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    settings_command(window).trigger()
    window.settings_window.category_list.setCurrentRow(5)

    settings_command(window).trigger()

    assert window.findChildren(SettingsWindow) == [window.settings_window]
    assert current(window) is SettingsCategory.MAP  # where it was left
    assert window.settings_window.isVisible()


def test_the_categories_are_listed_in_order(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)

    assert window.settings_window.categories == (
        SettingsCategory.PROFILE,
        SettingsCategory.GARMIN,
        SettingsCategory.SPORT_WEIGHTS,
        SettingsCategory.TRAINING_PLAN,
        SettingsCategory.NUTRITION,
        SettingsCategory.MAP,
        SettingsCategory.LANGUAGE,
    )
    assert category_names(window) == [
        "Profile",
        "Garmin account",
        "Sport weights",
        "Training plan",
        "Nutrition",
        "Map",
        "Language",
    ]
    assert [a.text() for a in window.menuBar().actions()].count("Language") == 0


def test_choosing_a_category_shows_its_page(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    settings_command(window).trigger()
    shown = window.settings_window

    for row, category in enumerate(shown.categories):
        shown.category_list.setCurrentRow(row)
        assert shown.current_category is category
        assert shown.page(category).isVisible()
        assert [
            c for c in shown.categories if c is not category and shown.page(c).isVisible()
        ] == []


def test_the_close_button_and_escape_hide_the_window_when_nothing_is_unsaved(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    settings_command(window).trigger()

    window.settings_window.close_button.click()
    assert not window.settings_window.isVisible()

    settings_command(window).trigger()
    QTest.keyClick(window.settings_window, Qt.Key.Key_Escape)
    assert not window.settings_window.isVisible()


# --- Without a profile ---------------------------------------


def test_the_pages_of_the_profile_say_why_they_are_empty_and_the_others_work(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    shown = window.settings_window

    for category in (
        SettingsCategory.PROFILE,
        SettingsCategory.SPORT_WEIGHTS,
        SettingsCategory.TRAINING_PLAN,
        SettingsCategory.NUTRITION,
    ):
        window.show_settings_page(category)
        page = shown.page(category)
        assert page.form is None
        assert page.notice_text == NO_PROFILE
    window.show_settings_page(SettingsCategory.MAP)
    assert isinstance(shown.page(SettingsCategory.MAP).form, MapSettingsDialog)
    assert shown.page(SettingsCategory.LANGUAGE).form is not None


def test_the_garmin_account_page_is_off_without_a_profile(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    section = window.garmin.account_section

    assert section.status_text == "Open a profile to see its Garmin account."
    assert not section.connect_button.isEnabled()
    assert section.connect_button.toolTip() == NO_PROFILE
    assert not section.auto_sync_check.isEnabled()


# --- The Garmin account --------------------------------------


def test_the_garmin_account_is_connected_and_the_preference_is_set_from_its_own_page(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_profile(qtbot, tmp_path, settings, make_fakes())
    window.show_settings_page(SettingsCategory.GARMIN)
    section = window.garmin.account_section
    assert window.settings_window.page(SettingsCategory.GARMIN).isAncestorOf(section)
    assert window.settings_window.category_label(SettingsCategory.GARMIN) == "Garmin account"

    assert section.status_text == "No Garmin account is connected to this profile."
    assert section.connect_button.isEnabled()
    assert not section.disconnect_button.isEnabled()
    assert section.auto_sync_check.isChecked()

    section.connect_button.click()  # opens the same sign-in dialog as the command
    qtbot.waitUntil(lambda: window.garmin.login_dialog is not None, timeout=WAIT_MS)
    window.garmin.login_dialog.reject()  # type: ignore[union-attr]
    section.auto_sync_check.click()

    assert not window.controller.sync.auto_sync_enabled
    assert not window.garmin.auto_sync_action.isChecked()  # one preference, two views


def test_the_garmin_menu_has_no_account_settings_and_the_profile_page_no_garmin_section(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_profile(qtbot, tmp_path, settings, make_fakes())
    menu = next(a.menu() for a in window.menuBar().actions() if a.text() == "Garmin")
    assert isinstance(menu, QMenu)

    assert "Garmin account settings..." not in [a.text() for a in menu.actions()]
    assert window.garmin.sync_action in menu.actions()
    profile_page = window.settings_window.page(SettingsCategory.PROFILE)
    assert not profile_page.isAncestorOf(window.garmin.account_section)
    wait_account(qtbot, window, AccountStatus.NOT_LINKED)


# --- Language ------------------------------------------------


def test_choosing_italian_is_remembered_and_the_page_says_when_it_applies(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    form = language_form(window)
    assert form.button(Language.ENGLISH).isChecked()
    assert not note_shown(window)

    form.button(Language.ITALIAN).click()

    assert note_shown(window)
    assert saved_language(settings) is Language.ITALIAN
    assert window.windowTitle() == "Ponos Coach"  # does not change live
    restarted = compose(tmp_path / "second", settings, language=Language.ITALIAN)
    qtbot.addWidget(restarted)
    assert language_form(restarted).button(Language.ITALIAN).isChecked()
    assert not note_shown(restarted)  # it is the language that is running


def test_choosing_the_running_language_again_says_nothing_and_is_remembered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    form = language_form(window)
    form.button(Language.ITALIAN).click()

    form.button(Language.ENGLISH).click()

    assert not note_shown(window)
    assert saved_language(settings) is Language.ENGLISH


# --- Training plan: the threshold test protocol --------------------------------------------------


def test_the_threshold_protocol_is_chosen_in_the_training_plan_page_and_remembered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    section = window.settings_window.page(SettingsCategory.TRAINING_PLAN).findChild(
        ThresholdProtocolSection
    )
    assert section is not None
    assert section.button(ThresholdProtocol.THIRTY_MINUTES).isChecked()

    section.button(ThresholdProtocol.TWENTY_MINUTES).click()

    assert settings.value("threshold/protocol") == ThresholdProtocol.TWENTY_MINUTES.value


# --- Unsaved edits -------------------------------------------


def edit_weight(window: MainWindow, qtbot: QtBot, text: str) -> None:
    dialog = open_edit_dialog(qtbot, window)
    dialog.weight_edit.clear()
    QTest.keyClicks(dialog.weight_edit, text)


def test_a_page_with_unsaved_edits_is_marked_and_the_mark_goes_when_it_is_saved(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    shown = window.settings_window
    window.edit_profile_action.trigger()
    dialog = open_edit_dialog(qtbot, window)
    assert shown.category_label(SettingsCategory.PROFILE) == "Profile"

    edit_weight(window, qtbot, "72.4")

    assert shown.category_label(SettingsCategory.PROFILE) == "Profile *"
    assert shown.category_label(SettingsCategory.SPORT_WEIGHTS) == "Sport weights"
    click(dialog.save_button)
    wait_profile_saved(qtbot, window)
    assert shown.category_label(SettingsCategory.PROFILE) == "Profile"


def test_closing_with_unsaved_edits_asks_with_a_button_for_each_verb(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    edit_weight(window, qtbot, "72.4")

    window.settings_window.close()

    box = open_message_box(qtbot, window, "settingsUnsaved")
    assert {b.text() for b in box.buttons()} == {"Save", "Discard", "Keep editing"}
    assert box.informativeText() == "Unsaved: Profile."
    assert window.settings_window.isVisible()  # nothing is lost before the answer


def test_keep_editing_leaves_the_window_open_with_the_edits(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    edit_weight(window, qtbot, "72.4")
    window.settings_window.close()

    answer_unsaved(qtbot, window, "Keep editing")

    assert window.settings_window.isVisible()
    dialog = window.profile_dialogs.edit_dialog
    assert dialog is not None and dialog.has_unsaved_changes()


def test_save_in_the_question_saves_the_page_and_then_closes_the_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    edit_weight(window, qtbot, "72.4")
    window.settings_window.close()

    answer_unsaved(qtbot, window, "Save")

    qtbot.waitUntil(lambda: not window.settings_window.isVisible(), timeout=WAIT_MS)
    reopened = open_edit_dialog(qtbot, window)
    assert reopened.weight_edit.text() == "72.4"
    assert not reopened.has_unsaved_changes()


def test_a_save_that_the_page_refuses_keeps_the_window_open_and_does_not_close_it_later(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    edit_weight(window, qtbot, "abc")
    window.settings_window.close()

    answer_unsaved(qtbot, window, "Save")

    dialog = window.profile_dialogs.edit_dialog
    assert dialog is not None
    assert dialog.error_text(EditField.WEIGHT) != ""
    assert window.settings_window.isVisible()
    dialog.weight_edit.clear()
    QTest.keyClicks(dialog.weight_edit, "72.4")
    click(dialog.save_button)
    wait_profile_saved(qtbot, window)
    assert window.settings_window.isVisible()  # the user asked for Save once, not for closing


def test_discard_in_the_question_drops_the_edits_and_closes_the_window(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    before = open_edit_dialog(qtbot, window).weight_edit.text()
    edit_weight(window, qtbot, "72.4")
    window.settings_window.close()

    answer_unsaved(qtbot, window, "Discard")

    assert not window.settings_window.isVisible()
    assert window.settings_window.category_label(SettingsCategory.PROFILE) == "Profile"
    window.edit_profile_action.trigger()
    dialog = open_edit_dialog(qtbot, window)
    qtbot.waitUntil(lambda: dialog.weight_edit.text() == before, timeout=WAIT_MS)


def test_a_key_typed_and_not_saved_marks_the_map_page(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    window.show_settings_page(SettingsCategory.MAP)
    form = window.settings_window.page(SettingsCategory.MAP).form
    assert isinstance(form, MapSettingsDialog)

    QTest.keyClicks(form.key_field, "not-a-saved-key")

    assert window.settings_window.category_label(SettingsCategory.MAP) == "Map *"
    form.key_field.clear()
    assert window.settings_window.category_label(SettingsCategory.MAP) == "Map"


def test_the_pages_that_apply_at_once_are_never_marked(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = empty_window(qtbot, tmp_path, settings)
    language_form(window).button(Language.ITALIAN).click()

    assert not window.settings_window.has_unsaved_changes()


def test_quitting_with_unsaved_settings_asks_first_and_keeps_the_app_open(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    edit_weight(window, qtbot, "72.4")

    window.close()
    answer_unsaved(qtbot, window, "Keep editing")

    assert not window.is_closed
    assert window.settings_window.isVisible()
    window.settings_window.page(SettingsCategory.PROFILE).discard()
    close_window(qtbot, window)


# --- The commands that used to open dialogs ------------------------------------------------------


def test_the_profile_commands_open_their_pages(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    shown = window.settings_window

    open_weights_dialog(qtbot, window)
    assert current(window) is SettingsCategory.SPORT_WEIGHTS
    open_edit_dialog(qtbot, window)
    assert current(window) is SettingsCategory.PROFILE
    assert shown.isVisible()


def test_the_plan_and_map_commands_open_their_pages(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    qtbot.waitUntil(window.plan.actions["training"].isEnabled, timeout=WAIT_MS)

    window.plan.actions["training"].trigger()
    assert current(window) is SettingsCategory.TRAINING_PLAN
    qtbot.waitUntil(lambda: window.plan.training_dialog is not None, timeout=WAIT_MS)
    window.map_page.settings_requested.emit()  # the "Open map settings..." button of the notice
    assert current(window) is SettingsCategory.MAP
    window.map_ui.actions["clear_cache"].trigger()
    assert current(window) is SettingsCategory.MAP
    assert window.map_ui.settings_dialog is not None
    assert len(window.map_ui.settings_dialog.open_boxes) == 1


def test_the_edit_profile_link_of_the_day_page_opens_the_profile_page(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_days(qtbot, tmp_path, settings)
    page = open_day(qtbot, window, DAWN_AND_LUNCH_DAY)

    page.edit_profile_requested.emit()

    assert window.settings_window.isVisible()
    assert current(window) is SettingsCategory.PROFILE
