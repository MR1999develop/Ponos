# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composition Root: the only module allowed to import every package."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PySide6.QtCore import QCoreApplication, QLocale, QSettings, QTimeZone
from PySide6.QtWidgets import QApplication

import ponos_coach
from ponos_coach.activities.ports import FitFileSource
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.load.use_cases import FitnessParameters
from ponos_coach.storage.fit_folder import FitFolder
from ponos_coach.ui.bootstrap import prepare_qt
from ponos_coach.ui.main_window import APP_NAME, MainWindow
from ponos_coach.ui.overview_controller import OverviewController
from ponos_coach.ui.translations import (
    Language,
    TranslationReport,
    app_translations_dir,
    choose_language,
    install_translators,
    qt_translations_dir,
)

_ORGANIZATION = "PonosCoach"
_FALLBACK_ZONE = ZoneInfo("UTC")

# PROVVISORI dello scheletro (fase 0): in fase 1 li sostituisce il profilo dell'utente.
PROVISIONAL_PHYSIOLOGY = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(165), Sex.MALE)


@dataclass(frozen=True, slots=True)
class CommandLine:
    """Opzioni della riga di comando."""

    fit_folder: Path | None
    language: Language | None = None


def parse_command_line(argv: Sequence[str]) -> CommandLine:
    """Legge le opzioni; un uso scorretto termina con `SystemExit(2)` (argparse)."""
    parser = argparse.ArgumentParser(prog="ponos-coach", description=ponos_coach.__doc__)
    parser.add_argument(
        "--fit-folder",
        type=Path,
        default=None,
        metavar="PATH",
        help="folder of FIT files to load at start (provisional, until profiles exist)",
    )
    parser.add_argument(
        "--lang",
        choices=[language.value for language in Language],
        default=None,
        help="force the interface language (default: the system language)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {ponos_coach.__version__}"
    )
    args = parser.parse_args(list(argv))
    return CommandLine(args.fit_folder, None if args.lang is None else Language(args.lang))


def resolve_user_timezone(zone_id: str) -> tuple[ZoneInfo, bool]:
    """Fuso dell'utente da un id IANA; ripiego su UTC (secondo valore vero) se non si trova."""
    try:
        return ZoneInfo(zone_id), False
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return _FALLBACK_ZONE, True


def setup_translations(
    app: QCoreApplication,
    command_line: CommandLine,
    *,
    ui_languages: Sequence[str] | None = None,
    app_dir: Path | None = None,
    qt_dir: Path | None = None,
) -> TranslationReport:
    """Sceglie la lingua (`--lang` o sistema) e installa i traduttori, prima della finestra."""
    if ui_languages is None:
        ui_languages = QLocale.system().uiLanguages()
    language = choose_language(command_line.language, ui_languages)
    return install_translators(
        app,
        language,
        app_dir=app_translations_dir() if app_dir is None else app_dir,
        qt_dir=qt_translations_dir() if qt_dir is None else qt_dir,
    )


def _fit_folder_source(folder: Path) -> FitFileSource:
    return FitFolder(folder)


def compose_main_window(
    command_line: CommandLine,
    *,
    settings: QSettings,
    system_timezone_id: str | None = None,
    clock: Callable[[ZoneInfo], date] | None = None,
) -> MainWindow:
    """Costruisce la finestra con tutte le dipendenze (richiede una `QApplication`).

    `system_timezone_id` e `clock` servono a rendere deterministico l'avvio nei test; in
    produzione sono il fuso di sistema e la data di oggi in quel fuso.
    """
    if system_timezone_id is None:
        system_timezone_id = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii", "replace")
    zone, fell_back = resolve_user_timezone(system_timezone_id)
    today_in = clock or (lambda tz: datetime.now(tz).date())
    parameters = FitnessParameters(PROVISIONAL_PHYSIOLOGY, zone)
    controller = OverviewController(_fit_folder_source, parameters, lambda: today_in(zone))
    notices = [QCoreApplication.translate("Main", "Provisional heart-rate settings")]
    if fell_back:
        notices.append(QCoreApplication.translate("Main", "Time zone not recognised: using UTC"))
    window = MainWindow(controller, settings, notices)
    if command_line.fit_folder is not None:
        window.open_folder(command_line.fit_folder)
    return window


def main(argv: Sequence[str] | None = None) -> int:
    """Start the application and return the process exit code."""
    try:
        command_line = parse_command_line(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_request:
        return exit_request.code if isinstance(exit_request.code, int) else 1
    prepare_qt()
    app = QApplication([APP_NAME])
    app.setOrganizationName(_ORGANIZATION)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(ponos_coach.__version__)
    setup_translations(app, command_line)
    window = compose_main_window(command_line, settings=QSettings())
    window.show()
    return app.exec()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
