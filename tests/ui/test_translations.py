# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Interface language: choice, translator installation, fallback to English."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QAction
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import compose_main_window, main, parse_command_line, setup_translations
from ponos_coach.ui.translations import (
    APP_CATALOG,
    LANGUAGE_KEY,
    Language,
    TranslationReport,
    choose_language,
    qt_translations_dir,
    save_language,
    saved_language,
)

MINIMAL_CATALOG = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="it" sourcelanguage="en">
<context>
    <name>MainWindow</name>
    <message>
        <source>Quit</source>
        <translation>Esci</translation>
    </message>
</context>
</TS>
"""


@pytest.fixture
def installed(qapp: QCoreApplication) -> Iterator[list[TranslationReport]]:
    """Registry of the translators installed by a test: at test end all are removed."""
    reports: list[TranslationReport] = []
    yield reports
    for report in reports:
        for translator in report.translators:
            qapp.removeTranslator(translator)


def build_catalog(folder: Path) -> Path:
    """Compile with `pyside6-lrelease` a minimal catalog (only "Quit" -> "Esci")."""
    source = folder / "ponos_it.ts"
    source.write_text(MINIMAL_CATALOG, encoding="utf-8")
    scripts = Path(sys.executable).parent
    tool = shutil.which("pyside6-lrelease", path=str(scripts)) or shutil.which("pyside6-lrelease")
    assert tool is not None
    target = folder / APP_CATALOG
    subprocess.run(  # noqa: S603 - project tool, fixed arguments
        [tool, str(source), "-qm", str(target)], check=True, capture_output=True, timeout=60
    )
    return target


def build_real_catalog(folder: Path) -> Path:
    """Compile with `pyside6-lrelease` the project's real Italian catalog."""
    source = Path(__file__).resolve().parents[2] / "src" / "ponos_coach" / "i18n" / "ponos_it.ts"
    scripts = Path(sys.executable).parent
    tool = shutil.which("pyside6-lrelease", path=str(scripts)) or shutil.which("pyside6-lrelease")
    assert tool is not None
    target = folder / APP_CATALOG
    subprocess.run(  # noqa: S603 - project tool, fixed arguments
        [tool, str(source), "-qm", str(target)], check=True, capture_output=True, timeout=60
    )
    return target


def window_action_texts(tmp_path: Path, qtbot: QtBot) -> set[str]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = compose_main_window(
        parse_command_line([]),
        settings=settings,
        config_dir=tmp_path / "config",
        documents_dir=tmp_path / "documents",
        system_timezone_id="UTC",
        clock=lambda _zone: date(2026, 2, 1),
        map_cache_dir=tmp_path / "map-cache",
    )
    qtbot.addWidget(window)
    return {action.text() for action in window.findChildren(QAction)}


@pytest.mark.parametrize(
    ("forced", "saved", "expected"),
    [
        (None, None, Language.ENGLISH),  # default: English, never the system language
        (None, Language.ITALIAN, Language.ITALIAN),
        (None, Language.ENGLISH, Language.ENGLISH),
        (Language.ENGLISH, Language.ITALIAN, Language.ENGLISH),  # `--lang` takes precedence
        (Language.ITALIAN, Language.ENGLISH, Language.ITALIAN),
        (Language.ITALIAN, None, Language.ITALIAN),
    ],
)
def test_language_is_the_forced_one_else_the_saved_choice_else_english(
    forced: Language | None, saved: Language | None, expected: Language
) -> None:
    assert choose_language(forced, saved) is expected


def test_the_system_language_does_not_decide(qapp: QCoreApplication) -> None:
    report = setup_translations(qapp, parse_command_line([]), app_dir=Path(), qt_dir=Path())

    assert report.language is Language.ENGLISH
    assert report.translators == ()


def test_the_saved_choice_is_remembered_and_read_back(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert saved_language(settings) is None

    save_language(settings, Language.ITALIAN)

    again = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    assert saved_language(again) is Language.ITALIAN


@pytest.mark.parametrize("raw", ["fr", "", "IT", "it ", "../it", "1", "x" * 1000])
def test_a_damaged_saved_language_counts_as_not_chosen(tmp_path: Path, raw: str) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue(LANGUAGE_KEY, raw)

    assert saved_language(settings) is None


def test_a_saved_value_that_is_not_text_counts_as_not_chosen(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue(LANGUAGE_KEY, 7)

    assert saved_language(settings) is None


def test_lang_option_selects_the_language_and_rejects_unknown_codes() -> None:
    assert parse_command_line([]).language is None
    assert parse_command_line(["--lang", "it"]).language is Language.ITALIAN
    assert parse_command_line(["--lang", "en"]).language is Language.ENGLISH
    assert main(["--lang", "fr"]) == 2


def test_with_lang_it_and_a_catalog_the_window_text_is_italian(
    qtbot: QtBot, tmp_path: Path, installed: list[TranslationReport], qapp: QCoreApplication
) -> None:
    build_catalog(tmp_path)

    report = setup_translations(
        qapp,
        parse_command_line(["--lang", "it"]),
        app_dir=tmp_path,
        qt_dir=qt_translations_dir(),
    )
    installed.append(report)
    texts = window_action_texts(tmp_path, qtbot)

    assert report.language is Language.ITALIAN
    assert report.app_loaded
    assert report.qt_loaded  # Qt's standard dialogs in Italian
    assert "Esci" in texts
    assert "Quit" not in texts


def test_without_a_catalog_the_app_starts_in_english_and_logs_it_once(
    qtbot: QtBot,
    tmp_path: Path,
    installed: list[TranslationReport],
    qapp: QCoreApplication,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="ponos_coach.ui.translations"):
        report = setup_translations(
            qapp,
            parse_command_line(["--lang", "it"]),
            app_dir=tmp_path,  # empty: ponos_it.qm is missing
            qt_dir=qt_translations_dir(),
        )
    installed.append(report)
    texts = window_action_texts(tmp_path, qtbot)

    assert not report.app_loaded
    assert report.qt_loaded
    assert "Quit" in texts
    warnings = [r for r in caplog.records if r.name == "ponos_coach.ui.translations"]
    assert len(warnings) == 1
    assert APP_CATALOG in warnings[0].getMessage()


def test_a_corrupt_catalog_is_treated_as_missing(
    tmp_path: Path, installed: list[TranslationReport], qapp: QCoreApplication
) -> None:
    (tmp_path / APP_CATALOG).write_bytes(b"not a catalog")

    report = setup_translations(
        qapp, parse_command_line(["--lang", "it"]), app_dir=tmp_path, qt_dir=tmp_path
    )
    installed.append(report)

    assert not report.app_loaded
    assert not report.qt_loaded
    assert report.translators == ()


def test_english_installs_nothing_even_when_the_catalog_exists(
    tmp_path: Path, installed: list[TranslationReport], qapp: QCoreApplication
) -> None:
    build_catalog(tmp_path)

    report = setup_translations(
        qapp,
        parse_command_line(["--lang", "en"]),
        saved=Language.ITALIAN,
        app_dir=tmp_path,
        qt_dir=tmp_path,
    )
    installed.append(report)

    assert report.translators == ()
    assert QCoreApplication.translate("MainWindow", "Quit") == "Quit"


def test_the_real_italian_catalog_translates_the_new_menus_and_keeps_language_names(
    qtbot: QtBot, tmp_path: Path, installed: list[TranslationReport], qapp: QCoreApplication
) -> None:
    build_real_catalog(tmp_path)

    report = setup_translations(
        qapp,
        parse_command_line([]),
        saved=Language.ITALIAN,
        app_dir=tmp_path,
        qt_dir=qt_translations_dir(),
    )
    installed.append(report)
    texts = window_action_texts(tmp_path, qtbot)

    assert report.language is Language.ITALIAN
    assert report.app_loaded
    assert {"Profilo", "Lingua", "Modifica profilo...", "Pesi degli sport..."} <= texts
    assert {"Dettaglio attività...", "Aiuto"} <= texts
    assert {"English", "Italiano"} <= texts  # the name of a language is not translated
    assert "Edit profile..." not in texts
