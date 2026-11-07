# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composition Root: the only module allowed to import every package."""

from __future__ import annotations

import argparse
import logging
import os
import random
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import platformdirs
from PySide6.QtCore import QCoreApplication, QSettings, QTimeZone
from PySide6.QtWidgets import QApplication

import ponos_coach
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    DEFAULT_POLICY,
    FailureReason,
    FileFormat,
    SessionTokens,
    SyncPolicy,
)
from ponos_coach.activities.ports import GarminAccount, GarminSession
from ponos_coach.activities.sync import ExtractedOriginal, OriginalExtractionError
from ponos_coach.load.use_cases import Decoder
from ponos_coach.platform.folder_kind import SystemFolderKindDetector
from ponos_coach.platform.notifier import DesktopNotifier
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import (
    JsonProfileRegistry,
    RegistryBusyError,
    RegistryCorruptError,
    RegistryFormatTooNewError,
)
from ponos_coach.platform.token_store import create_token_store
from ponos_coach.profiles.domain import ProfileId
from ponos_coach.profiles.ports import TokenStore, TokenStoreUnavailableError
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
from ponos_coach.ui.main_window import (
    APP_NAME,
    FolderPicker,
    MainWindow,
    NewProfileDefaults,
    native_folder_picker,
)
from ponos_coach.ui.sync_preferences import QSettingsSyncPreferences
from ponos_coach.ui.sync_worker import MFA_WAIT_S, SyncServices, SystemNotifier
from ponos_coach.ui.translations import (
    Language,
    TranslationReport,
    app_translations_dir,
    choose_language,
    install_translators,
    qt_translations_dir,
    saved_language,
)
from ponos_coach.ui.workspace import (
    ImportSources,
    ProfileUseCases,
    WorkspaceController,
    WorkspaceServices,
)
from ponos_coach.zipread import ZipReadError, ZipReason, read_activity_entry

_ORGANIZATION = "PonosCoach"
_FALLBACK_ZONE = ZoneInfo("UTC")
_LOG = logging.getLogger("ponos_coach.main")  # sotto "ponos_coach": lo raccoglie il file di log

# Perché uno ZIP scaricato non dà un file di attività -> causa definitiva della voce del giornale.
_FAILURE_BY_ZIP_REASON: Final[Mapping[ZipReason, FailureReason]] = {
    ZipReason.ARCHIVE_INVALID: FailureReason.ARCHIVE_INVALID,
    ZipReason.ARCHIVE_LIMIT: FailureReason.ARCHIVE_INVALID,
    ZipReason.ENCRYPTED: FailureReason.ARCHIVE_INVALID,
    ZipReason.UNSAFE_NAME: FailureReason.ARCHIVE_INVALID,
    ZipReason.TOO_LARGE: FailureReason.FILE_TOO_LARGE,
    ZipReason.NESTED_ARCHIVE: FailureReason.ARCHIVE_INVALID,
    ZipReason.NO_ACTIVITY: FailureReason.UNSUPPORTED_FORMAT,  # per esempio solo un TCX
    ZipReason.MULTIPLE_ACTIVITIES: FailureReason.ARCHIVE_INVALID,
}


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
        help="force the interface language (default: the saved choice, else English)",
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
    saved: Language | None = None,
    app_dir: Path | None = None,
    qt_dir: Path | None = None,
) -> TranslationReport:
    """Sceglie la lingua (`--lang`, poi la scelta salvata, poi l'inglese) e installa i traduttori,
    prima della finestra."""
    language = choose_language(command_line.language, saved)
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


class _SystemSyncClock:
    """Orologio della sincronizzazione: istanti in UTC e durate con l'orologio monotono."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


def extract_original_activity(content: bytes) -> ExtractedOriginal:
    """Il file di attività dentro lo ZIP scaricato da Garmin, con i tetti di `zipread`.

    Raises:
        OriginalExtractionError: lo ZIP non dà un file di attività; la causa è sempre definitiva.
    """
    try:
        entry = read_activity_entry(content)
    except ZipReadError as error:
        raise OriginalExtractionError(_FAILURE_BY_ZIP_REASON[error.reason]) from error
    return ExtractedOriginal(entry.content, FileFormat(entry.suffix.removeprefix(".")))


class _LazyGarminAccount:
    """L'account vero, su `garminconnect`, creato al primo uso.

    `garminconnect` e `curl_cffi` pesano e servono solo a chi collega o sincronizza: l'avvio
    dell'app non li importa. Le operazioni dell'account girano una alla volta; il lucchetto
    protegge comunque la creazione.
    """

    def __init__(self) -> None:
        self._account: GarminAccount | None = None
        self._lock = threading.Lock()

    def _real(self) -> GarminAccount:
        with self._lock:
            if self._account is None:
                from ponos_coach.garmin.account import GarminConnectAccount  # noqa: PLC0415
                from ponos_coach.garmin.connect import ConnectClientFactory  # noqa: PLC0415

                self._account = GarminConnectAccount(ConnectClientFactory())
            return self._account

    def login(
        self, email: str, password: str, prompt_mfa_code: Callable[[], str | None]
    ) -> GarminSession:
        """Vedi `GarminAccount.login`."""
        return self._real().login(email, password, prompt_mfa_code)

    def resume(self, tokens: SessionTokens) -> GarminSession:
        """Vedi `GarminAccount.resume`."""
        return self._real().resume(tokens)


def _default_garmin_account() -> GarminAccount:
    """L'account vero, che importa la libreria solo alla prima operazione."""
    return _LazyGarminAccount()


def _default_token_store(data_dir: Path) -> TokenStore | None:
    """I token nel portachiavi del sistema; `None` se non si può usare (per esempio Linux senza
    Secret Service): allora l'account non si collega e l'import da file resta disponibile."""
    try:
        return create_token_store(data_dir)
    except TokenStoreUnavailableError:
        _LOG.warning("The system keychain is not available: Garmin accounts cannot be connected")
        return None


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
    documents_dir: Path | None = None,
    folder_picker: FolderPicker | None = None,
    system_timezone_id: str | None = None,
    clock: Callable[[ZoneInfo], date] | None = None,
    now_utc: Callable[[], datetime] | None = None,
    import_sources: ImportSources | None = None,
    decode: Decoder | None = None,
    log_dir: Path | None = None,
    garmin_account: GarminAccount | None = None,
    token_store: TokenStore | None = None,
    notifier: SystemNotifier | None = None,
    sync_policy: SyncPolicy | None = None,
    data_dir: Path | None = None,
    mfa_wait_s: float = MFA_WAIT_S,
    language: Language = Language.ENGLISH,
) -> MainWindow:
    """Costruisce la finestra con tutte le dipendenze (richiede una `QApplication`).

    `config_dir` (elenco dei profili) e `documents_dir` (punto di partenza del selettore di
    cartella la prima volta) valgono i luoghi dell'utente se omessi; `folder_picker` è il selettore
    di cartella (finto nei test); `system_timezone_id`, `clock`, `now_utc` e
    `import_sources` e `decode` rendono deterministico l'avvio nei test; `log_dir` è la cartella
    dei log che il menu Aiuto apre. `garmin_account`, `token_store`, `notifier` e `sync_policy`
    sostituiscono Garmin, il portachiavi, la notifica di sistema e le pause della
    sincronizzazione (finti nei test); `data_dir` è la cartella dati locale dell'app, dove stanno
    i token cifrati su Windows; `mfa_wait_s` è quanto si aspetta il codice MFA; `language` è la
    lingua già installata (il menu Lingua la mostra spuntata). Non apre nessun profilo: lo fa
    `MainWindow.start()`, dopo `show()`.
    """
    del command_line  # per ora nessuna opzione cambia la composizione
    if config_dir is None:
        config_dir = Path(platformdirs.user_config_dir("PonosCoach", appauthor=False))
    if documents_dir is None:
        documents_dir = Path(platformdirs.user_documents_dir())
    if log_dir is None:
        log_dir = _default_log_dir()
    if system_timezone_id is None:
        system_timezone_id = bytes(QTimeZone.systemTimeZoneId().data()).decode("ascii", "replace")
    zone, fell_back = resolve_user_timezone(system_timezone_id)
    today_in = clock or (lambda tz: datetime.now(tz).date())
    utc_now = now_utc or (lambda: datetime.now(UTC))
    if data_dir is None:
        data_dir = Path(platformdirs.user_data_dir("PonosCoach", appauthor=False))

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
        sync=SyncServices(
            account=garmin_account or _default_garmin_account(),
            tokens=token_store or _default_token_store(data_dir),
            extract_original=extract_original_activity,
            decode=decode_stored_activity,
            clock=_SystemSyncClock(),
            make_rng=random.Random,
            notifier=notifier or DesktopNotifier(APP_NAME),
            policy=sync_policy or DEFAULT_POLICY,
            mfa_wait_s=mfa_wait_s,
            preferences=QSettingsSyncPreferences(settings),
        ),
        errors=ErrorContext(_registry_problem, config_dir),
    )
    controller = WorkspaceController(services)
    defaults = NewProfileDefaults(
        documents_dir, folder_picker or native_folder_picker, zone.key, fell_back, today_in
    )
    window = MainWindow(controller, settings, defaults, log_folder=log_dir, language=language)
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
    settings = QSettings()
    report = setup_translations(app, command_line, saved=saved_language(settings))
    notifier = ErrorNotifier(app)
    install_exception_hooks(notifier.notify)
    window = compose_main_window(
        command_line, settings=settings, log_dir=log_dir, language=report.language
    )
    notifier.occurred.connect(window.show_unexpected_error)
    app.aboutToQuit.connect(window.release_web_engine)  # pagine e profilo web, prima dei figli
    window.show()
    window.start()
    exit_code = app.exec()
    # Se `exec` ritorna senza che la finestra abbia chiuso (sessione di sistema, `quit`), nessun
    # thread dei dati deve restare in esecuzione: distruggerlo a processo in uscita abortisce.
    DataThread.stop_all()
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
