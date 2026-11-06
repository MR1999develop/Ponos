# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composition Root: the only module allowed to import every package."""

from __future__ import annotations

import argparse
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import platformdirs
from PySide6.QtCore import QCoreApplication, QLocale, QSettings, QTimeZone
from PySide6.QtWidgets import QApplication

import ponos_coach
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.load.use_cases import Decoder
from ponos_coach.platform.folder_kind import SystemFolderKindDetector
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import (
    JsonProfileRegistry,
    RegistryBusyError,
    RegistryCorruptError,
    RegistryFormatTooNewError,
)
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.use_cases import (
    CreateProfile,
    DeleteProfileFolder,
    ListProfiles,
    OpenProfileFromFolder,
    OpenRegisteredProfile,
    PeekProfile,
    ProfilePorts,
    RemoveProfileFromList,
)
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.import_scan import FilesImportSource, FolderImportSource
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.ui.bootstrap import prepare_qt
from ponos_coach.ui.data_thread import DataThread
from ponos_coach.ui.diagnostics import ErrorNotifier, configure_logging, install_exception_hooks
from ponos_coach.ui.errors import ErrorContext, RegistryProblem
from ponos_coach.ui.main_window import APP_NAME, MainWindow, NewProfileDefaults
from ponos_coach.ui.translations import (
    Language,
    TranslationReport,
    app_translations_dir,
    choose_language,
    install_translators,
    qt_translations_dir,
)
from ponos_coach.ui.workspace import (
    ImportSources,
    ProfileUseCases,
    WorkspaceController,
    WorkspaceServices,
)

_ORGANIZATION = "PonosCoach"
_FALLBACK_ZONE = ZoneInfo("UTC")


@dataclass(frozen=True, slots=True)
class CommandLine:
    """Opzioni della riga di comando."""

    language: Language | None = None


def parse_command_line(argv: Sequence[str]) -> CommandLine:
    """Legge le opzioni; un uso scorretto termina con `SystemExit(2)` (argparse)."""
    parser = argparse.ArgumentParser(prog="ponos-coach", description=ponos_coach.__doc__)
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
    return CommandLine(None if args.lang is None else Language(args.lang))


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


class _SystemClock:
    """Orologio di produzione per `imported_at`: l'istante corrente in UTC."""

    def now(self) -> datetime:
        return datetime.now(UTC)


def _registry_problem(error: BaseException) -> RegistryProblem | None:
    """Quale guasto dell'elenco dei profili è (i suoi tipi sono dell'adattatore, non della ui)."""
    if isinstance(error, RegistryFormatTooNewError):
        return RegistryProblem.TOO_NEW
    if isinstance(error, RegistryBusyError):
        return RegistryProblem.BUSY
    if isinstance(error, RegistryCorruptError):
        return RegistryProblem.CORRUPT
    return None


def _default_log_dir() -> Path:
    return Path(platformdirs.user_log_dir("PonosCoach", appauthor=False))


def _default_import_sources() -> ImportSources:
    return ImportSources(files=FilesImportSource, folder=FolderImportSource)


def compose_main_window(  # noqa: PLR0913 - ogni argomento è una dipendenza volatile iniettabile
    command_line: CommandLine,
    *,
    settings: QSettings,
    config_dir: Path | None = None,
    profiles_dir: Path | None = None,
    system_timezone_id: str | None = None,
    clock: Callable[[ZoneInfo], date] | None = None,
    now_utc: Callable[[], datetime] | None = None,
    import_sources: ImportSources | None = None,
    decode: Decoder | None = None,
    log_dir: Path | None = None,
) -> MainWindow:
    """Costruisce la finestra con tutte le dipendenze (richiede una `QApplication`).

    `config_dir` (elenco dei profili) e `profiles_dir` (cartella proposta per i profili nuovi)
    valgono i luoghi dell'utente se omessi; `system_timezone_id`, `clock`, `now_utc` e
    `import_sources` e `decode` rendono deterministico l'avvio nei test; `log_dir` è la cartella
    dei log che il menu Aiuto apre. Non apre nessun profilo:
    lo fa `MainWindow.start()`, dopo `show()`.
    """
    del command_line  # per ora nessuna opzione cambia la composizione
    if config_dir is None:
        config_dir = Path(platformdirs.user_config_dir("PonosCoach", appauthor=False))
    if profiles_dir is None:
        # Cartella dati locale, non Documenti: su Windows Documenti è spesso sincronizzata
        # con OneDrive, che l'app sconsiglia per SQLite (PLAN, sezione 3).
        profiles_dir = Path(platformdirs.user_data_dir("PonosCoach", appauthor=False)) / "profiles"
    if log_dir is None:
        log_dir = _default_log_dir()
    if system_timezone_id is None:
        system_timezone_id = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii", "replace")
    zone, fell_back = resolve_user_timezone(system_timezone_id)
    today_in = clock or (lambda tz: datetime.now(tz).date())
    utc_now = now_utc or (lambda: datetime.now(UTC))

    ports = ProfilePorts(
        JsonProfileRegistry(config_dir),
        LocalProfileFolders(SystemFolderKindDetector(os.environ)),
        SqliteProfileStorage(),
    )
    use_cases = ProfileUseCases(
        create=CreateProfile(ports, utc_now, lambda: ProfileId(uuid.uuid4())),
        open_from_folder=OpenProfileFromFolder(ports, utc_now),
        open_registered=OpenRegisteredProfile(ports, utc_now),
        list_all=ListProfiles(ports),
        remove=RemoveProfileFromList(ports),
        delete=DeleteProfileFolder(ports),
        peek=PeekProfile(ports),
    )
    services = WorkspaceServices(
        ports=ports,
        profiles=use_cases,
        archive_for=FileArchive.in_profile_folder,
        import_sources=import_sources or _default_import_sources(),
        decode=decode or decode_stored_activity,
        clock=_SystemClock(),
        today=today_in,
        errors=ErrorContext(_registry_problem, config_dir),
    )
    controller = WorkspaceController(services)
    defaults = NewProfileDefaults(profiles_dir, zone.key, fell_back, today_in)
    window = MainWindow(controller, settings, defaults, log_folder=log_dir)
    controller.setParent(window)
    return window


def main(argv: Sequence[str] | None = None) -> int:
    """Start the application and return the process exit code."""
    try:
        command_line = parse_command_line(sys.argv[1:] if argv is None else argv)
    except SystemExit as exit_request:
        return exit_request.code if isinstance(exit_request.code, int) else 1
    log_dir = _default_log_dir()
    configure_logging(log_dir, version=ponos_coach.__version__)
    prepare_qt()
    app = QApplication([APP_NAME])
    app.setOrganizationName(_ORGANIZATION)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(ponos_coach.__version__)
    setup_translations(app, command_line)
    notifier = ErrorNotifier(app)
    install_exception_hooks(notifier.notify)
    window = compose_main_window(command_line, settings=QSettings(), log_dir=log_dir)
    notifier.occurred.connect(window.show_unexpected_error)
    window.show()
    window.start()
    exit_code = app.exec()
    # Se `exec` ritorna senza che la finestra abbia chiuso (sessione di sistema, `quit`), nessun
    # thread dei dati deve restare in esecuzione: distruggerlo a processo in uscita abortisce.
    DataThread.stop_all()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
