# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Workspace controller: profiles, import and overview, all on the data thread.

The controller lives on the UI thread and holds the presentation state; each job on the
profile (open, create, import, overview, switch, close) is a `Job` run by the
`DataThread`, which owns the SQLite connection. Outcomes come back as signals. An
"old" outcome (of an already replaced overview) is ignored. Garmin synchronisation belongs to
`SyncController`, with its own thread and connection; from here only the
consequences are decided (profile locked while downloading, overview recomputed at the end).
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
from ponos_coach.nutrition.food_table import FoodTable
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


# --- Dependencies composed by the Composition Root ----------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportSources:
    """How to create an import's source: from chosen files or from a folder."""

    files: Callable[[Sequence[Path]], ImportSource]
    folder: Callable[[Path], ImportSource]


@dataclass(frozen=True, slots=True)
class ProfileUseCases:
    """The profile use cases that the UI calls."""

    create: CreateProfile
    open_from_folder: OpenProfileFromFolder
    open_registered: OpenRegisteredProfile
    list_all: ListProfiles
    remove: RemoveProfileFromList
    delete: DeleteProfileFolder
    peek: PeekProfile


@dataclass(frozen=True, slots=True)
class WorkspaceServices:
    """Everything the controller receives from the Composition Root."""

    ports: ProfilePorts
    profiles: ProfileUseCases
    archive_for: Callable[[Path], ActivityArchive]
    import_sources: ImportSources
    decode: Decoder
    clock: Clock
    today: Callable[[ZoneInfo], date]
    sync: SyncServices
    foods: Callable[[], FoodTable]
    errors: ErrorContext = field(default_factory=ErrorContext)


# --- Values the UI receives ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProfileView:
    """The open profile, as the UI sees it."""

    profile_id: ProfileId
    name: ProfileName
    folder: Path
    timezone_name: str
    warnings: tuple[FolderWarning, ...]

    @property
    def zone(self) -> ZoneInfo:
        """The profile's time zone (the name has already been validated by the domain)."""
        return ZoneInfo(self.timezone_name)


@dataclass(frozen=True, slots=True)
class ImportProgress:
    """Progress of an import; `total` is 0 until the source examination has finished."""

    done: int
    total: int


@dataclass(frozen=True, slots=True)
class FolderCheck:
    """Outcome of examining a folder; `inspection` is `None` if it could not be read."""

    folder: Path
    inspection: FolderInspection | None


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    """What the deletion confirmation shows; `activities` is `None` if they cannot be counted."""

    entry: RegistryEntry
    activities: int | None


@dataclass(frozen=True, slots=True)
class ProfileAccess:
    """The open profile, for jobs that run on the data thread (`run_on_profile`).

    Never leave the session to the UI thread: it is needed only inside the job. `today` is
    today's date in the profile's time zone.
    """

    session: ProfileSession
    archive: ActivityArchive
    zone: ZoneInfo
    decode: Decoder
    today: Callable[[], date]


# --- State that lives only on the data thread ------------------------------------------------


class NoProfileOpenError(Exception):
    """A job on the profile started without an open profile."""


@dataclass(frozen=True, slots=True)
class _OpenProfile:
    session: ProfileSession
    archive: ActivityArchive
    zone: ZoneInfo


class _DataState:
    """The open profile. Touched only by jobs on the data thread (and by its closing)."""

    def __init__(self) -> None:
        self._current: _OpenProfile | None = None

    def require(self) -> _OpenProfile:
        if self._current is None:
            raise NoProfileOpenError
        return self._current

    def replace(self, new: _OpenProfile) -> None:
        """Set the new profile and close the old one (a failure while closing is logged)."""
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
    except Exception as error:  # the old profile must not block the new one
        _LOG.error("Closing the previous profile failed: %s", exception_chain(error))


def _remove_orphan_temporaries(archive: ActivityArchive) -> None:
    """Remove the temporaries left by an interrupted write; only the count is logged.

    A failure here does not prevent opening the profile: the temporaries stay for next time.
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
    """How many activities the profile has (read-only, without opening or migrating it), or
    `None`."""
    try:
        return peek(profile_id).activity_count
    except (ProfileStorageError, ProfileRegistryError):  # unreadable: the confirmation says so
        return None


# --- Controller -----------------------------------------------------------------------------

type _DoneHandler = Callable[[int, JobOutcome], None]
type _ProgressHandler = Callable[[int, int, int], None]


@dataclass(frozen=True, slots=True)
class _Pending:
    on_done: _DoneHandler
    on_progress: _ProgressHandler | None


class WorkspaceController(QObject):
    """Workspace state and start of the jobs on the data thread."""

    profiles_changed = Signal(object)  # tuple[RegistryEntry, ...], most recent first
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
        self._thread = DataThread(self._data.close)  # no parent: see `DataThread`
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
        """The open profile, if any."""
        return self._profile

    @property
    def profiles(self) -> tuple[RegistryEntry, ...]:
        """The profiles in the list, most recent first."""
        return self._entries

    @property
    def foods(self) -> Callable[[], FoodTable]:
        """Reads the food table shipped with the app (called on the data thread)."""
        return self._services.foods

    @property
    def sync(self) -> SyncController:
        """The Garmin account and synchronisation of the open profile."""
        return self._sync

    @property
    def overview_state(self) -> OverviewState:
        """Overview state."""
        return self._overview_state

    @property
    def import_progress(self) -> ImportProgress | None:
        """Progress of the import in progress, or `None`."""
        return self._import_progress

    @property
    def is_opening_profile(self) -> bool:
        """True while a profile is opening, being created or being switched."""
        return self._opening

    @property
    def is_closing(self) -> bool:
        """True after `begin_shutdown`: no new job starts any more."""
        return self._closing

    @property
    def has_stopped(self) -> bool:
        """True if the data thread and the account thread have finished (profile closed)."""
        return self._thread.has_stopped and self._sync.has_stopped

    @property
    def can_change_profile(self) -> bool:
        """A profile can be created, opened, switched, removed or deleted (not while
        importing or synchronising: the profile is in use)."""
        return self.can_import_now and not self._sync.is_syncing

    @property
    def can_import_now(self) -> bool:
        """No opening, import or closing in progress (a synchronisation does not count)."""
        return not self._closing and not self._opening and self._import_progress is None

    @property
    def can_import(self) -> bool:
        """An import can be started, even while synchronising (separate connections)."""
        return self._profile is not None and self.can_import_now

    @property
    def can_edit_profile(self) -> bool:
        """Measurements and weights can be changed: profile open and no opening, import, closing
        or overview computation in progress (the job would wait in the queue)."""
        return self._profile is not None and self.can_import_now and self._overview_handle is None

    @property
    def can_stop(self) -> bool:
        """There is something to stop: an import or the overview computation."""
        return self._import_progress is not None or self._overview_handle is not None

    # --- Comandi ----------------------------------------------------------------------

    def start(self) -> None:
        """Open the last used profile; without profiles ask for the guided first start."""
        if self._closing:
            return
        self._set_opening(True)
        self._set_overview(Loading(0, 0))
        self._submit(self._list_job(), self._after_startup_list)

    def create_profile(self, request: NewProfileRequest) -> bool:
        """Create and open a profile. False (nothing happens) if it cannot be done now."""
        return self._begin_open(lambda: self._services.profiles.create(request))

    def open_profile_folder(self, folder: Path) -> bool:
        """Open the profile that is in a folder."""
        if self._profile is not None and folder == self._profile.folder:
            return False
        return self._begin_open(lambda: self._services.profiles.open_from_folder(folder))

    def switch_profile(self, profile_id: ProfileId) -> bool:
        """Open a profile from the list."""
        if self._profile is not None and profile_id == self._profile.profile_id:
            return False
        return self._begin_open(lambda: self._services.profiles.open_registered(profile_id))

    def remove_profile(self, profile_id: ProfileId) -> bool:
        """Remove a profile from the list (the folder and the data stay)."""
        if not self.can_change_profile:
            return False

        def job(_context: JobContext) -> object:
            self._services.profiles.remove(profile_id)
            return tuple(self._services.profiles.list_all())

        self._submit(job, self._after_list_changed)
        return True

    def request_deletion(self, profile_id: ProfileId) -> bool:
        """Prepare the deletion confirmation (name, folder, number of activities)."""
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
        """Delete the folder of a profile that is not open and remove it from the list."""
        if not self.can_change_profile:
            return False

        def job(_context: JobContext) -> object:
            self._services.profiles.delete(profile_id)
            return tuple(self._services.profiles.list_all())

        self._submit(job, self._after_list_changed)
        return True

    def inspect_folder(self, folder: Path) -> None:
        """Examine a folder (new, empty, full; local or network) without blocking the UI."""
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
        """Import chosen files (FIT, GPX, ZIP) into the open profile."""
        chosen = tuple(paths)
        return self._begin_import(lambda: self._services.import_sources.files(chosen))

    def import_folder(self, folder: Path) -> bool:
        """Import a folder with its subfolders into the open profile."""
        return self._begin_import(lambda: self._services.import_sources.folder(folder))

    def recalculate_overview(self) -> bool:
        """Recompute the open profile's overview."""
        if self._profile is None or not self.can_change_profile:
            return False
        self._start_overview()
        return True

    def request_overview_refresh(self) -> None:
        """Recompute the overview after a change of measurements or weights.

        If it cannot be done now (synchronisation in progress) it is done at the end of the round.
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
        """Run `work` on the data thread on the open profile; the outcome arrives on the UI thread.

        False (nothing starts) if there is no open profile or the app is closing. Whoever calls
        decides what to do with the outcome, including the error.
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
        """The user message of an error, with the Composition Root's data."""
        return describe_error(error, self._services.errors)

    def cancel_current(self) -> None:
        """Stop the import in progress or, if there is none, the overview computation."""
        if self._import_handle is not None:
            self._import_handle.cancel()
        elif self._overview_handle is not None:
            self._overview_handle.cancel()

    def begin_shutdown(self) -> None:
        """First step of closing: cancel the jobs and ask the data thread to close
        the profile. It does not wait: whoever closes checks `has_stopped`. From here no job starts
        any more; can be repeated."""
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
        """Run `work` on the data thread and deliver the result on the UI thread.

        For pure, heavy jobs that do not touch the profile (the chart figure). False if
        the app is closing. A cancelled job delivers nothing.
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
        """Queue a job; `None` if the app is closing."""
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

    # --- Profiles: start and opening ----------------------------------------------------

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
            self._start_overview()  # the previous profile stays open: resume the overview

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
        # `_set_overview` has already notified, but before the job was registered: without this
        # the commands that depend on the computation in progress (Edit profile, Weights) stay
        # active.
        self.busy_changed.emit()

    def _cancel_overview(self) -> None:
        if self._overview_handle is not None:
            self._overview_handle.cancel()
            self._overview_handle = None  # its outcome, when it arrives, is "old"

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
        self._cancel_overview()  # recompute when the import is done
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
        """At the end of a round with new activities the overview is recomputed (on the data
        thread); if an import is in progress it will do so at its end."""
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
    """List of profiles after an opening; if it cannot be read, the opening is still valid."""
    try:
        return tuple(services.profiles.list_all())
    except ProfileRegistryError as error:
        _LOG.error("The profile list could not be read after opening: %s", exception_chain(error))
        return ()
