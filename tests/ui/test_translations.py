# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lingua dell'interfaccia: scelta, installazione dei traduttori, ripiego sull'inglese."""

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
    Language,
    TranslationReport,
    choose_language,
    qt_translations_dir,
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
    """Registro dei traduttori installati da un test: a fine test si tolgono tutti."""
    reports: list[TranslationReport] = []
    yield reports
    for report in reports:
        for translator in report.translators:
            qapp.removeTranslator(translator)


def build_catalog(folder: Path) -> Path:
    """Compila con `pyside6-lrelease` un catalogo minimo (solo "Quit" -> "Esci")."""
    source = folder / "ponos_it.ts"
    source.write_text(MINIMAL_CATALOG, encoding="utf-8")
    scripts = Path(sys.executable).parent
    tool = shutil.which("pyside6-lrelease", path=str(scripts)) or shutil.which("pyside6-lrelease")
    assert tool is not None
    target = folder / APP_CATALOG
    subprocess.run(  # noqa: S603 - strumento del progetto, argomenti fissi
        [tool, str(source), "-qm", str(target)], check=True, capture_output=True, timeout=60
    )
    return target


def window_action_texts(tmp_path: Path, qtbot: QtBot) -> set[str]:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    window = compose_main_window(
        parse_command_line([]),
        settings=settings,
        system_timezone_id="UTC",
        clock=lambda _zone: date(2026, 2, 1),
    )
    qtbot.addWidget(window)
    return {action.text() for action in window.findChildren(QAction)}


@pytest.mark.parametrize(
    ("forced", "system", "expected"),
    [
        (None, ["it-IT", "en-US"], Language.ITALIAN),
        (None, ["it"], Language.ITALIAN),
        (None, ["IT_ch"], Language.ITALIAN),
        (None, ["en-US", "it-IT"], Language.ENGLISH),  # conta la preferita, non la presenza
        (None, ["de-DE"], Language.ENGLISH),
        (None, ["C"], Language.ENGLISH),
        (None, [], Language.ENGLISH),
        (Language.ENGLISH, ["it-IT"], Language.ENGLISH),
        (Language.ITALIAN, ["en-US"], Language.ITALIAN),
        (Language.ITALIAN, [], Language.ITALIAN),
    ],
)
def test_language_is_the_forced_one_else_the_first_system_ui_language(
    forced: Language | None, system: list[str], expected: Language
) -> None:
    assert choose_language(forced, system) is expected


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
        ui_languages=["en-US"],
        app_dir=tmp_path,
        qt_dir=qt_translations_dir(),
    )
    installed.append(report)
    texts = window_action_texts(tmp_path, qtbot)

    assert report.language is Language.ITALIAN
    assert report.app_loaded
    assert report.qt_loaded  # i dialoghi standard di Qt in italiano
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
            app_dir=tmp_path,  # vuota: manca ponos_it.qm
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
        ui_languages=["it-IT"],
        app_dir=tmp_path,
        qt_dir=tmp_path,
    )
    installed.append(report)

    assert report.translators == ()
    assert QCoreApplication.translate("MainWindow", "Quit") == "Quit"
