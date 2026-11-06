# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Controller dell'area di lavoro: profili, import e panoramica, tutto nel thread dei dati.

Il controller vive nel thread della UI e tiene lo stato di presentazione; ogni lavoro sul
profilo (apertura, creazione, import, panoramica, cambio, chiusura) è un `Job` eseguito dal
`DataThread`, che possiede la connessione SQLite. Gli esiti tornano come segnali. Un esito
"vecchio" (di una panoramica già sostituita) si ignora. La sincronizzazione con Garmin è del
`SyncController`, con un thread e una connessione propri; da qui si decidono solo le
conseguenze (profilo bloccato mentre scarica, panoramica ricalcolata alla fine).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from PySide6.QtCore import QObject, Signal, Slot

from ponos_coach.activities.ports import ActivityArchive, ArchiveError, Clock, ImportSource
from ponos_coach.activities.use_cases import ImportSummary, import_activities
from ponos_coach.load.use_cases import (
    ComputeProfileOverview,
    Decoder,
    OverviewCancelledError,
    ProfileOverview,
)
from ponos_coach.profiles.domain import FolderInspection, ProfileId, ProfileName, RegistryEntry
from ponos_coach.profiles.ports import (
    ProfileFolderError,
    ProfileRegistryError,
    ProfileStorageError,
)
from ponos_coach.profiles.use_cases import (
    CreateProfile,
    DeleteProfileFolder,
    FolderWarning,
    ListProfiles,
    NewProfileRequest,
    OpenProfileFromFolder,
    OpenRegisteredProfile,
    PeekProfile,
    ProfilePorts,
    ProfileSession,
    RemoveProfileFromList,
)
from ponos_coach.ui.data_thread import (
    DataThread,
    DataThreadClosedError,
    JobCancelledError,
    JobContext,
    JobFailed,
    JobHandle,
    JobOutcome,
    JobSucceeded,
)
from ponos_coach.ui.diagnostics import exception_chain
from ponos_coach.ui.errors import ErrorContext, ErrorMessage, describe_error
from ponos_coach.ui.import_report import ImportReport
from ponos_coach.ui.overview_state import Cancelled, Failed, Idle, Loading, OverviewState, Ready
from ponos_coach.ui.sync_controller import SyncController
from ponos_coach.ui.sync_model import SyncResult, new_activity_count
from ponos_coach.ui.sync_worker import SyncServices

_LOG = logging.getLogger(__name__)

__all__ = [
    "DeletionPreview",
    "FolderCheck",
    "ImportProgress",
    "ImportSources",
    "ProfileAccess",
    "ProfileUseCases",
    "ProfileView",
    "WorkspaceController",
    "WorkspaceServices",
]


# --- Dipendenze composte dal Composition Root ----------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportSources:
    """Come si crea la sorgente di un import: da file scelti o da una cartella."""

    files: Callable[[Sequence[Path]], ImportSource]
    folder: Callable[[Path], ImportSource]


@dataclass(frozen=True, slots=True)
class ProfileUseCases:
    """I casi d'uso dei profili che la UI chiama."""

    create: CreateProfile
    open_from_folder: OpenProfileFromFolder
    open_registered: OpenRegisteredProfile
    list_all: ListProfiles
    remove: RemoveProfileFromList
    delete: DeleteProfileFolder
    peek: PeekProfile


@dataclass(frozen=True, slots=True)
class WorkspaceServices:
    """Tutto ciò che il controller riceve dal Composition Root."""

    ports: ProfilePorts
    profiles: ProfileUseCases
    archive_for: Callable[[Path], ActivityArchive]
    import_sources: ImportSources
    decode: Decoder
    clock: Clock
    today: Callable[[ZoneInfo], date]
    sync: SyncServices
    errors: ErrorContext = field(default_factory=ErrorContext)


# --- Valori che la UI riceve ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileView:
    """Il profilo aperto, come lo vede la UI."""

    profile_id: ProfileId
    name: ProfileName
    folder: Path
    timezone_name: str
    warnings: tuple[FolderWarning, ...]

    @property
    def zone(self) -> ZoneInfo:
        """Fuso del profilo (il nome è già stato validato dal dominio)."""
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class ImportProgress:
    """Avanzamento di un import; `total` è 0 finché l'esame della sorgente non è finito."""

    done: int
    total: int


@dataclass(frozen=True, slots=True)
class FolderCheck:
    """Esito dell'esame di una cartella; `inspection` è `None` se non si è potuta leggere."""

    folder: Path
    inspection: FolderInspection | None


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    """Ciò che la conferma di cancellazione mostra; `activities` è `None` se non contabili."""

    entry: RegistryEntry
    activities: int | None


@dataclass(frozen=True, slots=True)
class ProfileAccess:
    """Il profilo aperto, per i lavori che girano nel thread dei dati (`run_on_profile`).

    Non lasciare mai la sessione al thread della UI: serve solo dentro il lavoro. `today` è la
    data di oggi nel fuso del profilo.
    """

    session: ProfileSession
    archive: ActivityArchive
    zone: ZoneInfo
    decode: Decoder
    today: Callable[[], date]


# --- Stato che vive solo nel thread dei dati ------------------------------------------------


class NoProfileOpenError(Exception):
    """Un lavoro sul profilo è partito senza un profilo aperto."""


@dataclass(frozen=True, slots=True)
class _OpenProfile:
    session: ProfileSession
    archive: ActivityArchive
    zone: ZoneInfo


class _DataState:
    """Il profilo aperto. Si tocca solo da lavori del thread dei dati (e dalla sua chiusura)."""

    def __init__(self) -> None:
        self._current: _OpenProfile | None = None

    def require(self) -> _OpenProfile:
        if self._current is None:
            raise NoProfileOpenError
        return self._current

    def replace(self, new: _OpenProfile) -> None:
        """Mette il nuovo profilo e chiude il vecchio (un guasto nella chiusura si registra)."""
        old, self._current = self._current, new
        if old is not None:
            _close_quietly(old)

    def close(self) -> None:
        old, self._current = self._current, None
        if old is not None:
            old.session.close()


def _close_quietly(profile: _OpenProfile) -> None:
    try:
        profile.session.close()
    except Exception as error:  # il profilo vecchio non deve bloccare quello nuovo
        _LOG.error("Closing the previous profile failed: %s", exception_chain(error))


def _remove_orphan_temporaries(archive: ActivityArchive) -> None:
    """Toglie i temporanei lasciati da una scrittura interrotta; si registra solo il numero.

    Un guasto qui non impedisce di aprire il profilo: i temporanei restano per la prossima volta.
    """
    try:
        removed = archive.remove_orphan_temporaries()
    except ArchiveError as error:
        _LOG.error("Removing the orphan temporaries failed: %s", exception_chain(error))
        return
    if removed:
        _LOG.info("Removed %d orphan temporary file(s) of the archive", removed)


@dataclass(frozen=True, slots=True)
class _Opened:
    view: ProfileView
    entries: tuple[RegistryEntry, ...]


def _count_activities(peek: PeekProfile, profile_id: ProfileId) -> int | None:
    """Quante attività ha il profilo (sola lettura, senza aprirlo né migrarlo), o `None`."""
    try:
        return peek(profile_id).activity_count
    except (ProfileStorageError, ProfileRegistryError):  # illeggibile: la conferma lo dice
        return None


# --- Controller -----------------------------------------------------------------------------

type _DoneHandler = Callable[[int, JobOutcome], None]
type _ProgressHandler = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class _Pending:
    on_done: _DoneHandler
    on_progress: _ProgressHandler | None


class WorkspaceController(QObject):
    """Stato dell'area di lavoro e avvio dei lavori nel thread dei dati."""

    profiles_changed = Signal(object)  # tuple[RegistryEntry, ...], dal più recente
    profile_changed = Signal(object)  # ProfileView
    overview_changed = Signal(object)  # OverviewState
    import_progress_changed = Signal(object)  # ImportProgress | None
    import_finished = Signal(object)  # ImportReport
    operation_failed = Signal(object)  # ErrorMessage
    deletion_requested = Signal(object)  # DeletionPreview
    folder_inspected = Signal(object)  # FolderCheck
    first_run_requested = Signal()
    busy_changed = Signal()

    def __init__(self, services: WorkspaceServices, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._services = services
        self._data = _DataState()
        self._thread = DataThread(self._data.close)  # senza genitore: vedi `DataThread`
        self._thread.progressed.connect(self._on_progress)
        self._thread.completed.connect(self._on_completed)
        self._sync = SyncController(
            services.sync, services.ports.storage, services.archive_for, self
        )
        self._sync.state_changed.connect(self.busy_changed)
        self._sync.sync_finished.connect(self._after_sync)
        self._pending: dict[int, _Pending] = {}
        self._profile: ProfileView | None = None
        self._entries: tuple[RegistryEntry, ...] = ()
        self._overview_state: OverviewState = Idle()
        self._opening = False
        self._closing = False
        self._overview_stale = False
        self._import_progress: ImportProgress | None = None
        self._overview_handle: JobHandle | None = None
        self._import_handle: JobHandle | None = None

    # --- Stato ------------------------------------------------------------------------

    @property
    def profile(self) -> ProfileView | None:
        """Il profilo aperto, se c'è."""
        return self._profile

    @property
    def profiles(self) -> tuple[RegistryEntry, ...]:
        """I profili dell'elenco, dal più recente."""
        return self._entries

    @property
    def sync(self) -> SyncController:
        """L'account Garmin e la sincronizzazione del profilo aperto."""
        return self._sync

    @property
    def overview_state(self) -> OverviewState:
        """Stato della panoramica."""
        return self._overview_state

    @property
    def import_progress(self) -> ImportProgress | None:
        """Avanzamento dell'import in corso, o `None`."""
        return self._import_progress

    @property
    def is_opening_profile(self) -> bool:
        """Vero mentre un profilo si apre, si crea o si cambia."""
        return self._opening

    @property
    def is_closing(self) -> bool:
        """Vero dopo `begin_shutdown`: nessun nuovo lavoro parte più."""
        return self._closing

    @property
    def has_stopped(self) -> bool:
        """Vero se il thread dei dati e quello dell'account hanno finito (profilo chiuso)."""
        return self._thread.has_stopped and self._sync.has_stopped

    @property
    def can_change_profile(self) -> bool:
        """Si può creare, aprire, cambiare, rimuovere o cancellare un profilo (non mentre si
        importa né mentre si sincronizza: il profilo è in uso)."""
        return self.can_import_now and not self._sync.is_syncing

    @property
    def can_import_now(self) -> bool:
        """Nessuna apertura, import o chiusura in corso (una sincronizzazione non conta)."""
        return not self._closing and not self._opening and self._import_progress is None

    @property
    def can_import(self) -> bool:
        """Si può avviare un import, anche mentre si sincronizza (connessioni separate)."""
        return self._profile is not None and self.can_import_now

    @property
    def can_edit_profile(self) -> bool:
        """Si possono cambiare misure e pesi: profilo aperto e nessuna apertura, import, chiusura
        né calcolo della panoramica in corso (il lavoro aspetterebbe in coda)."""
        return self._profile is not None and self.can_import_now and self._overview_handle is None

    @property
    def can_stop(self) -> bool:
        """C'è qualcosa da fermare: un import o il calcolo della panoramica."""
        return self._import_progress is not None or self._overview_handle is not None

    # --- Comandi ----------------------------------------------------------------------

    def start(self) -> None:
        """Apre l'ultimo profilo usato; senza profili chiede il primo avvio guidato."""
        if self._closing:
            return
        self._set_opening(True)
        self._set_overview(Loading(0, 0))
        self._submit(self._list_job(), self._after_startup_list)

    def create_profile(self, request: NewProfileRequest) -> bool:
        """Crea e apre un profilo. Falso (nulla accade) se ora non si può."""
        return self._begin_open(lambda: self._services.profiles.create(request))

    def open_profile_folder(self, folder: Path) -> bool:
        """Apre il profilo che sta in una cartella."""
        if self._profile is not None and folder == self._profile.folder:
            return False
        return self._begin_open(lambda: self._services.profiles.open_from_folder(folder))

    def switch_profile(self, profile_id: ProfileId) -> bool:
        """Apre un profilo dell'elenco."""
        if self._profile is not None and profile_id == self._profile.profile_id:
            return False
        return self._begin_open(lambda: self._services.profiles.open_registered(profile_id))

    def remove_profile(self, profile_id: ProfileId) -> bool:
        """Toglie un profilo dall'elenco (la cartella e i dati restano)."""
        if not self.can_change_profile:
            return False

        def job(_context: JobContext) -> object:
            self._services.profiles.remove(profile_id)
            return tuple(self._services.profiles.list_all())

        self._submit(job, self._after_list_changed)
        return True

    def request_deletion(self, profile_id: ProfileId) -> bool:
        """Prepara la conferma di cancellazione (nome, cartella, numero di attività)."""
        if not self.can_change_profile:
            return False
        services = self._services

        def job(_context: JobContext) -> object:
            entry = next(
                (e for e in services.profiles.list_all() if e.profile_id == profile_id), None
            )
            if entry is None:
                return None
            return DeletionPreview(entry, _count_activities(services.profiles.peek, profile_id))

        self._submit(job, self._after_deletion_preview)
        return True

    def delete_profile_folder(self, profile_id: ProfileId) -> bool:
        """Cancella la cartella di un profilo non aperto e lo toglie dall'elenco."""
        if not self.can_change_profile:
            return False

        def job(_context: JobContext) -> object:
            self._services.profiles.delete(profile_id)
            return tuple(self._services.profiles.list_all())

        self._submit(job, self._after_list_changed)
        return True

    def inspect_folder(self, folder: Path) -> None:
        """Esamina una cartella (nuova, vuota, piena; locale o di rete) senza bloccare la UI."""
        if self._closing:
            return

        def job(_context: JobContext) -> object:
            try:
                return FolderCheck(folder, self._services.ports.folders.inspect(folder))
            except ProfileFolderError:
                return FolderCheck(folder, None)

        def done(_job_id: int, outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, FolderCheck):
                self.folder_inspected.emit(outcome.value)

        self._submit(job, done)

    def import_files(self, paths: Sequence[Path]) -> bool:
        """Importa file scelti (FIT, GPX, ZIP) nel profilo aperto."""
        chosen = tuple(paths)
        return self._begin_import(lambda: self._services.import_sources.files(chosen))

    def import_folder(self, folder: Path) -> bool:
        """Importa una cartella con le sue sottocartelle nel profilo aperto."""
        return self._begin_import(lambda: self._services.import_sources.folder(folder))

    def recalculate_overview(self) -> bool:
        """Ricalcola la panoramica del profilo aperto."""
        if self._profile is None or not self.can_change_profile:
            return False
        self._start_overview()
        return True

    def request_overview_refresh(self) -> None:
        """Ricalcola la panoramica dopo un cambio di misure o di pesi.

        Se ora non si può (sincronizzazione in corso) lo si fa alla fine del giro.
        """
        if self._profile is None or self._closing:
            return
        if self.can_change_profile:
            self._start_overview()
        else:
            self._overview_stale = True

    def run_on_profile(
        self,
        work: Callable[[ProfileAccess], object],
        on_done: Callable[[JobOutcome], None],
    ) -> bool:
        """Esegue `work` nel thread dei dati sul profilo aperto; l'esito arriva nel thread UI.

        Falso (nulla parte) se non c'è un profilo aperto o l'app si sta chiudendo. Chi chiama
        decide cosa fare dell'esito, compreso l'errore.
        """
        if self._profile is None:
            return False
        services = self._services
        data = self._data

        def job(_context: JobContext) -> object:
            current = data.require()
            access = ProfileAccess(
                current.session,
                current.archive,
                current.zone,
                services.decode,
                lambda: services.today(current.zone),
            )
            return work(access)

        def done(_job_id: int, outcome: JobOutcome) -> None:
            on_done(outcome)

        return self._submit(job, done) is not None

    def describe_failure(self, error: BaseException) -> ErrorMessage:
        """Il messaggio per l'utente di un errore, con i dati del Composition Root."""
        return describe_error(error, self._services.errors)

    def cancel_current(self) -> None:
        """Ferma l'import in corso o, se non c'è, il calcolo della panoramica."""
        if self._import_handle is not None:
            self._import_handle.cancel()
        elif self._overview_handle is not None:
            self._overview_handle.cancel()

    def begin_shutdown(self) -> None:
        """Primo tempo della chiusura: annulla i lavori e chiede al thread dei dati di chiudere
        il profilo. Non attende: chi chiude controlla `has_stopped`. Da qui nessun lavoro parte
        più; si può ripetere."""
        self._closing = True
        self._cancel_overview()
        if self._import_handle is not None:
            self._import_handle.cancel()
        self._sync.begin_shutdown()
        self._thread.request_shutdown()
        self.busy_changed.emit()

    def compute(
        self,
        work: Callable[[], object],
        on_done: Callable[[object], None],
        on_failed: Callable[[ErrorMessage], None],
    ) -> bool:
        """Esegue `work` nel thread dei dati e consegna il risultato nel thread della UI.

        Per lavori puri e pesanti che non toccano il profilo (la figura del grafico). Falso se
        l'app si sta chiudendo. Un lavoro annullato non consegna nulla.
        """

        def job(_context: JobContext) -> object:
            return work()

        def done(_job_id: int, outcome: JobOutcome) -> None:
            if isinstance(outcome, JobSucceeded):
                on_done(outcome.value)
            elif isinstance(outcome, JobFailed):
                on_failed(self.describe_failure(outcome.error))

        return self._submit(job, done) is not None

    # --- Lavori -----------------------------------------------------------------------

    def _submit(
        self,
        job: Callable[[JobContext], object],
        on_done: _DoneHandler,
        on_progress: _ProgressHandler | None = None,
    ) -> JobHandle | None:
        """Accoda un lavoro; `None` se l'app si sta chiudendo."""
        if self._closing:
            return None
        try:
            handle = self._thread.submit(job)
        except DataThreadClosedError:
            return None
        self._pending[handle.job_id] = _Pending(on_done, on_progress)
        return handle

    @Slot(int, int, int)
    def _on_progress(self, job_id: int, done: int, total: int) -> None:
        pending = self._pending.get(job_id)
        if pending is not None and pending.on_progress is not None:
            pending.on_progress(job_id, done, total)

    @Slot(int, object)
    def _on_completed(self, job_id: int, outcome: JobOutcome) -> None:
        pending = self._pending.pop(job_id, None)
        if pending is not None:
            pending.on_done(job_id, outcome)

    def _list_job(self) -> Callable[[JobContext], object]:
        def job(_context: JobContext) -> object:
            return tuple(self._services.profiles.list_all())

        return job

    def _open_job(self, opener: Callable[[], ProfileSession]) -> Callable[[JobContext], object]:
        services = self._services
        data = self._data

        def job(_context: JobContext) -> object:
            session = opener()
            try:
                info = session.info
                view = ProfileView(
                    info.profile_id, info.name, session.folder, info.timezone_name, session.warnings
                )
                archive = services.archive_for(session.folder)
                _remove_orphan_temporaries(archive)
                opened = _OpenProfile(session, archive, view.zone)
            except BaseException:
                session.close()
                raise
            data.replace(opened)
            return _Opened(view, _entries_or_empty(services))

        return job

    def _overview_job(self) -> Callable[[JobContext], object]:
        services = self._services
        data = self._data

        def job(context: JobContext) -> object:
            current = data.require()
            session = current.session
            compute = ComputeProfileOverview(
                session.activities,
                current.archive,
                session.physiology,
                session.load_cache,
                services.decode,
                sport_weights=session.sport_weights,
                timezone=current.zone,
                today=lambda: services.today(current.zone),
                is_cancelled=context.is_cancelled,
                report=context.report,
            )
            try:
                return compute.execute()
            except OverviewCancelledError as error:
                raise JobCancelledError from error

        return job

    def _import_job(
        self, make_source: Callable[[], ImportSource]
    ) -> Callable[[JobContext], object]:
        services = self._services
        data = self._data

        def job(context: JobContext) -> ImportSummary:
            current = data.require()
            source = make_source()
            try:
                return import_activities(
                    source,
                    current.archive,
                    current.session.activities,
                    services.clock,
                    report=context.report,
                    is_cancelled=context.is_cancelled,
                )
            finally:
                source.close()

        return job

    # --- Profili: avvio e apertura ----------------------------------------------------

    def _after_startup_list(self, _job_id: int, outcome: JobOutcome) -> None:
        if not isinstance(outcome, JobSucceeded) or not isinstance(outcome.value, tuple):
            self._finish_failed_open(outcome)
            return
        entries: tuple[RegistryEntry, ...] = outcome.value
        self._set_entries(entries)
        if not entries:
            self._set_opening(False)
            self._set_overview(Idle())
            self.first_run_requested.emit()
            return
        last = entries[0].profile_id
        self._submit(
            self._open_job(lambda: self._services.profiles.open_registered(last)),
            self._after_open,
        )

    def _begin_open(self, opener: Callable[[], ProfileSession]) -> bool:
        if not self.can_change_profile:
            return False
        self._cancel_overview()
        self._set_opening(True)
        self._submit(self._open_job(opener), self._after_open)
        return True

    def _after_open(self, _job_id: int, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, _Opened):
            opened = outcome.value
            self._profile = opened.view
            self._set_entries(opened.entries)
            self._set_opening(False)
            self.profile_changed.emit(opened.view)
            self._start_overview()
            self._sync.profile_opened(opened.view.profile_id, opened.view.folder)
        else:
            self._finish_failed_open(outcome)

    def _finish_failed_open(self, outcome: JobOutcome) -> None:
        self._set_opening(False)
        if isinstance(outcome, JobFailed):
            self.operation_failed.emit(self.describe_failure(outcome.error))
        if self._profile is None:
            self._set_overview(Idle())
        else:
            self._start_overview()  # il profilo di prima resta aperto: riprende la panoramica

    def _after_list_changed(self, _job_id: int, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, tuple):
            self._set_entries(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.operation_failed.emit(self.describe_failure(outcome.error))

    def _after_deletion_preview(self, _job_id: int, outcome: JobOutcome) -> None:
        if isinstance(outcome, JobSucceeded) and isinstance(outcome.value, DeletionPreview):
            self.deletion_requested.emit(outcome.value)
        elif isinstance(outcome, JobFailed):
            self.operation_failed.emit(self.describe_failure(outcome.error))

    # --- Panoramica -------------------------------------------------------------------

    def _start_overview(self) -> None:
        if self._closing:
            return
        self._cancel_overview()
        self._overview_stale = False
        self._set_overview(Loading(0, 0))
        self._overview_handle = self._submit(
            self._overview_job(), self._after_overview, self._on_overview_progress
        )

    def _cancel_overview(self) -> None:
        if self._overview_handle is not None:
            self._overview_handle.cancel()
            self._overview_handle = None  # il suo esito, quando arriva, è "vecchio"

    def _on_overview_progress(self, job_id: int, done: int, total: int) -> None:
        if self._is_current_overview(job_id) and isinstance(self._overview_state, Loading):
            self._set_overview(Loading(done, total))

    def _after_overview(self, job_id: int, outcome: JobOutcome) -> None:
        if not self._is_current_overview(job_id):
            return
        self._overview_handle = None
        match outcome:
            case JobSucceeded(ProfileOverview() as overview):
                self._set_overview(Ready(overview))
            case JobFailed(error):
                self._set_overview(Failed(describe_error(error)))
            case _:
                self._set_overview(Cancelled())

    def _is_current_overview(self, job_id: int) -> bool:
        return self._overview_handle is not None and self._overview_handle.job_id == job_id

    # --- Import -----------------------------------------------------------------------

    def _begin_import(self, make_source: Callable[[], ImportSource]) -> bool:
        if not self.can_import:
            return False
        self._cancel_overview()  # si ricalcola a import finito
        self._set_import_progress(ImportProgress(0, 0))
        self._import_handle = self._submit(
            self._import_job(make_source), self._after_import, self._on_import_progress
        )
        return self._import_handle is not None

    def _on_import_progress(self, _job_id: int, done: int, total: int) -> None:
        if self._import_progress is not None:
            self._set_import_progress(ImportProgress(done, total))

    def _after_import(self, _job_id: int, outcome: JobOutcome) -> None:
        self._import_handle = None
        self._set_import_progress(None)
        match outcome:
            case JobSucceeded(ImportSummary() as summary):
                self.import_finished.emit(ImportReport.from_summary(summary))
            case JobFailed(error):
                self.operation_failed.emit(describe_error(error))
            case _:
                pass
        self._start_overview()

    # --- Sincronizzazione -------------------------------------------------------------

    def _after_sync(self, result: SyncResult) -> None:
        """Alla fine di un giro con attività nuove la panoramica si ricalcola (nel thread dei
        dati); se un import è in corso lo farà lui alla sua fine."""
        if self._closing or self._profile is None or self._import_progress is not None:
            return
        if new_activity_count(result) > 0 or self._overview_stale:
            self._start_overview()

    # --- Notifiche di stato -----------------------------------------------------------

    def _set_entries(self, entries: tuple[RegistryEntry, ...]) -> None:
        self._entries = entries
        self.profiles_changed.emit(entries)

    def _set_overview(self, state: OverviewState) -> None:
        self._overview_state = state
        self.overview_changed.emit(state)
        self.busy_changed.emit()

    def _set_opening(self, opening: bool) -> None:
        self._opening = opening
        self.busy_changed.emit()

    def _set_import_progress(self, progress: ImportProgress | None) -> None:
        self._import_progress = progress
        self.import_progress_changed.emit(progress)
        self.busy_changed.emit()


def _entries_or_empty(services: WorkspaceServices) -> tuple[RegistryEntry, ...]:
    """Elenco dei profili dopo un'apertura; se non si legge, l'apertura vale lo stesso."""
    try:
        return tuple(services.profiles.list_all())
    except ProfileRegistryError as error:
        _LOG.error("The profile list could not be read after opening: %s", exception_chain(error))
        return ()
