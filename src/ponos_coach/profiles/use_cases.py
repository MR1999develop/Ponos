# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Casi d'uso dei profili: crea, apri (da cartella o dall'elenco), elenca, guarda (`peek`),
rimuovi dall'elenco, cancella la cartella.

È l'API che usa l'interfaccia. Ogni caso d'uso riceve le porte (`ProfilePorts`) e, se serve,
l'orologio nel costruttore; non fa I/O diretto. Un profilo aperto è una `ProfileSession`: tiene
il database e il lock finché non si chiama `close()`.

Le operazioni che toccano più cose (crea, apri) o riescono o lasciano le cose come prima: se
un passo fallisce, quelli già fatti si annullano, e un annullamento che a sua volta fallisce si
aggiunge come nota all'errore originale (che resta quello sollevato).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from types import TracebackType
from typing import Self

from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.load.domain import Physiology, PhysiologyEntry
from ponos_coach.load.ports import LoadCache, PhysiologyStore
from ponos_coach.profiles.domain import (
    FolderKind,
    FolderState,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
    RegistryEntry,
)
from ponos_coach.profiles.ports import (
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    OpenProfile,
    ProfileFolders,
    ProfileLock,
    ProfileNotFoundError,
    ProfileNotRegisteredError,
    ProfileRegistry,
    ProfileStorage,
)
from ponos_coach.profiles.timezones import require_known_timezone


class FolderWarning(Enum):
    """Avviso non bloccante sulla cartella di un profilo: SQLite rischia su questi posti."""

    NETWORK = "network"
    CLOUD_SYNCED = "cloud_synced"


class ProfileMismatchError(Exception):
    """La cartella registrata contiene il database di un altro profilo."""


@dataclass(frozen=True, slots=True)
class ProfilePorts:
    """Le tre porte che i casi d'uso dei profili usano."""

    registry: ProfileRegistry
    folders: ProfileFolders
    storage: ProfileStorage


def warnings_for(kind: FolderKind) -> tuple[FolderWarning, ...]:
    """Avvisi per il tipo di cartella (nessuno per locale o ignoto)."""
    if kind is FolderKind.NETWORK:
        return (FolderWarning.NETWORK,)
    if kind is FolderKind.CLOUD_SYNCED:
        return (FolderWarning.CLOUD_SYNCED,)
    return ()


class ProfileSession:
    """Un profilo aperto: informazioni, store del database e lock sulla cartella.

    `close()` chiude il database e poi rilascia il lock (sempre, anche se la chiusura del
    database fallisce); ripeterlo non ha effetti. Si usa anche come context manager.
    """

    def __init__(
        self,
        opened: OpenProfile,
        lock: ProfileLock,
        folder: Path,
        warnings: Sequence[FolderWarning],
    ) -> None:
        self._opened = opened
        self._lock = lock
        self._closed = False
        self.folder = folder
        self.warnings = tuple(warnings)

    @property
    def info(self) -> ProfileInfo:
        """Informazioni del profilo lette dal database."""
        return self._opened.info

    @property
    def activities(self) -> ActivityIndex:
        """Indice delle attività (valido finché il profilo è aperto)."""
        return self._opened.activities

    @property
    def physiology(self) -> PhysiologyStore:
        """Parametri fisiologici con data di validità."""
        return self._opened.physiology

    @property
    def load_cache(self) -> LoadCache:
        """Cache dei carichi."""
        return self._opened.load_cache

    def close(self) -> None:
        """Chiude il database e rilascia il lock."""
        if self._closed:
            return
        self._closed = True
        try:
            self._opened.close()
        finally:
            self._lock.release()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


class _Undo:
    """Passi di annullamento, eseguiti dall'ultimo al primo se l'operazione fallisce."""

    def __init__(self) -> None:
        self._steps: list[Callable[[], None]] = []

    def add(self, step: Callable[[], None]) -> None:
        self._steps.append(step)

    def run(self, failure: BaseException) -> None:
        for step in reversed(self._steps):
            try:
                step()
            except Exception as exc:
                failure.add_note(f"annullamento non riuscito: {type(exc).__name__}")


def _require_absolute(folder: Path) -> None:
    if not folder.is_absolute():
        raise FolderUnavailableError("la cartella del profilo deve essere un percorso assoluto")


# --- Crea ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewProfileRequest:
    """Dati per creare un profilo.

    `folder` assoluta, mancante o vuota; `timezone_name` un nome IANA; `physiology` vale dal
    giorno `today` (data di oggi nel fuso del profilo, decisa da chi chiama).
    """

    name: ProfileName
    folder: Path
    timezone_name: str
    physiology: Physiology
    today: date

    def __post_init__(self) -> None:
        if not self.folder.is_absolute():
            raise ValueError("la cartella del profilo deve essere un percorso assoluto")


class CreateProfile:
    """Crea un profilo: cartella privata, lock, database, parametri iniziali, voce nell'elenco.

    Se un passo fallisce non resta nessuna voce nell'elenco, il lock è rilasciato e si annulla
    solo ciò che ha fatto questa operazione. Se ha creato il database (la creazione è
    esclusiva: chi la perde non lo ha creato), si cancella la cartella con tutto il contenuto;
    altrimenti si toglie la cartella solo se è vuota a parte il file di lock. Un database o
    una cartella messi lì nel frattempo da un'altra istanza non si toccano mai. I genitori
    creati lungo il percorso restano.
    """

    def __init__(
        self,
        ports: ProfilePorts,
        now_utc: Callable[[], datetime],
        new_profile_id: Callable[[], ProfileId],
    ) -> None:
        self._ports = ports
        self._now_utc = now_utc
        self._new_profile_id = new_profile_id

    def __call__(self, request: NewProfileRequest) -> ProfileSession:
        """Crea il profilo e lo restituisce aperto.

        Raises:
            FolderNotEmptyError: la cartella esiste e non è vuota; nulla è stato creato.
            FolderUnavailableError: cartella non creabile o non scrivibile.
            ProfileInUseError: lock non ottenuto.
            ProfileAlreadyExistsError: un'altra istanza ha messo un database nella cartella
                prima di noi; il suo profilo non si tocca.
            ProfileStorageError: database non creato.
            ProfileRegistryError: elenco dei profili non scrivibile.
            ValueError: fuso orario sconosciuto (prima di toccare il disco).
        """
        require_known_timezone(request.timezone_name)
        ports = self._ports
        inspection = ports.folders.inspect(request.folder)
        if inspection.state is FolderState.NOT_EMPTY:
            raise FolderNotEmptyError(request.folder.name)
        trace = _CreationTrace(ports.folders, request.folder)
        try:
            session = self._create(request, inspection.kind, trace)
        except BaseException as failure:
            trace.roll_back(failure)
            raise
        return session

    def _create(
        self, request: NewProfileRequest, kind: FolderKind, trace: _CreationTrace
    ) -> ProfileSession:
        ports = self._ports
        folder = request.folder
        ports.folders.create_private_folder(folder)
        trace.folder_made = True
        lock = ports.folders.acquire_lock(folder)
        trace.lock = lock
        profile_id = self._new_profile_id()
        opened = ports.storage.create(folder, profile_id, request.name, request.timezone_name)
        trace.opened = opened
        trace.database_created = True
        opened.physiology.put(PhysiologyEntry(request.today, request.physiology))
        ports.registry.add(RegistryEntry(profile_id, folder, request.name, self._now_utc()))
        return ProfileSession(opened, lock, folder, warnings_for(kind))


class _CreationTrace:
    """Ciò che `CreateProfile` ha fatto finora, per annullare solo quello se qualcosa fallisce."""

    def __init__(self, folders: ProfileFolders, folder: Path) -> None:
        self._folders = folders
        self._folder = folder
        self.folder_made = False
        self.lock: ProfileLock | None = None
        self.opened: OpenProfile | None = None
        self.database_created = False

    def roll_back(self, failure: BaseException) -> None:
        """Annulla in ordine inverso: chiude il database, rilascia il lock, poi toglie la
        cartella. Un annullamento che fallisce si aggiunge come nota a `failure`."""
        steps: list[Callable[[], object]] = []
        if self.opened is not None:
            steps.append(self.opened.close)
        if self.lock is not None:
            steps.append(self.lock.release)  # prima della cancellazione, che prende il suo lock
        if self.database_created:
            steps.append(lambda: self._folders.delete_folder(self._folder))
        elif self.folder_made:
            steps.append(lambda: self._folders.remove_if_empty(self._folder))
        for step in steps:
            try:
                step()
            except Exception as exc:
                failure.add_note(f"annullamento non riuscito: {type(exc).__name__}")


# --- Apri ----------------------------------------------------------------------------------


class OpenProfileFromFolder:
    """Apre il profilo che sta in una cartella e lo mette (o lo aggiorna) nell'elenco.

    L'elenco prende il nome letto dal database, che è la fonte di verità; un profilo già
    elencato con un'altra cartella (spostato) o una cartella già elencata con un altro profilo
    (sostituita) si riallineano.
    """

    def __init__(self, ports: ProfilePorts, now_utc: Callable[[], datetime]) -> None:
        self._ports = ports
        self._now_utc = now_utc

    def __call__(self, folder: Path) -> ProfileSession:
        """Apre il profilo in `folder`.

        Raises:
            ProfileNotFoundError: la cartella manca o è vuota.
            NotAProfileFolderError: la cartella ha dei file ma non il database di un profilo
                (il lock non viene preso).
            ProfileInUseError: il profilo è aperto da un'altra istanza.
            FolderUnavailableError: cartella non leggibile.
            ProfileFormatTooNewError, ProfileCorruptError: database non apribile.
            ProfileRegistryError: elenco dei profili non aggiornabile.
        """
        return _open_session(self._ports, self._now_utc, folder, expected_id=None)


class OpenRegisteredProfile:
    """Apre un profilo dell'elenco, controllando che la cartella contenga proprio quel profilo."""

    def __init__(self, ports: ProfilePorts, now_utc: Callable[[], datetime]) -> None:
        self._ports = ports
        self._now_utc = now_utc

    def __call__(self, profile_id: ProfileId) -> ProfileSession:
        """Apre il profilo `profile_id` dalla sua cartella registrata.

        Raises:
            ProfileNotRegisteredError: id assente dall'elenco.
            ProfileMismatchError: la cartella contiene un altro profilo (nulla cambia).
            ProfileNotFoundError, NotAProfileFolderError, ProfileInUseError,
            FolderUnavailableError,
            ProfileFormatTooNewError, ProfileCorruptError, ProfileRegistryError: come
            `OpenProfileFromFolder`.
        """
        for entry in self._ports.registry.list_entries():
            if entry.profile_id == profile_id:
                return _open_session(
                    self._ports, self._now_utc, entry.folder, expected_id=profile_id
                )
        raise ProfileNotRegisteredError(str(profile_id))


def _open_session(
    ports: ProfilePorts,
    now_utc: Callable[[], datetime],
    folder: Path,
    expected_id: ProfileId | None,
) -> ProfileSession:
    _require_absolute(folder)
    inspection = ports.folders.inspect(folder)
    if inspection.state is not FolderState.NOT_EMPTY:
        raise ProfileNotFoundError(folder.name)  # niente lock in una cartella vuota o mancante
    if not inspection.has_profile_database:
        raise NotAProfileFolderError(folder.name)  # né in una che non ha il database
    undo = _Undo()
    try:
        lock = ports.folders.acquire_lock(folder)
        undo.add(lock.release)
        opened = ports.storage.open(folder)
        undo.add(opened.close)
        _check_expected(opened.info, expected_id)
        _register_opened(ports.registry, opened.info, folder, now_utc())
        return ProfileSession(opened, lock, folder, warnings_for(inspection.kind))
    except BaseException as failure:
        undo.run(failure)
        raise


def _check_expected(info: ProfileInfo, expected_id: ProfileId | None) -> None:
    if expected_id is not None and info.profile_id != expected_id:
        raise ProfileMismatchError("la cartella contiene un altro profilo")


def _register_opened(
    registry: ProfileRegistry, info: ProfileInfo, folder: Path, now: datetime
) -> None:
    """Porta l'elenco allo stato "questo profilo sta in questa cartella, usato adesso", con il
    nome letto dal database, in una sola scrittura (un profilo spostato o una cartella
    sostituita si riallineano insieme)."""
    registry.upsert(RegistryEntry(info.profile_id, folder, info.name, now))


# --- Elenco, rimozione, cancellazione ------------------------------------------------------


class ListProfiles:
    """Elenca i profili noti, dal più recentemente usato."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry

    def __call__(self) -> Sequence[RegistryEntry]:
        """Voci dell'elenco (nome in cache, cartella, ultimo uso).

        Raises:
            ProfileRegistryError: elenco illeggibile o di una versione futura.
        """
        return self._registry.list_entries()


class PeekProfile:
    """Legge in sola lettura le informazioni e il numero di attività di un profilo dell'elenco.

    Serve all'elenco dei profili: non apre il profilo, non prende il lock, non migra e non
    scrive nulla (nemmeno l'ultimo uso), quindi funziona anche se il profilo è aperto altrove.
    """

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry
        self._storage = ports.storage

    def __call__(self, profile_id: ProfileId) -> ProfilePeek:
        """Informazioni del profilo `profile_id` e numero delle sue attività.

        Raises:
            ProfileNotRegisteredError: id assente dall'elenco.
            ProfileNotFoundError: la cartella registrata non ha più il database.
            ProfileFormatTooNewError, ProfileCorruptError, ProfileStorageError: database non
                leggibile.
        """
        for entry in self._registry.list_entries():
            if entry.profile_id == profile_id:
                return self._storage.peek(entry.folder)
        raise ProfileNotRegisteredError(str(profile_id))


class RemoveProfileFromList:
    """Toglie un profilo dall'elenco senza toccare la sua cartella né i suoi dati."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry

    def __call__(self, profile_id: ProfileId) -> None:
        """Rimuove la voce.

        Raises:
            ProfileNotRegisteredError: id assente.
        """
        self._registry.remove(profile_id)


class DeleteProfileFolder:
    """Cancella la cartella di un profilo dell'elenco, con tutti i suoi dati, e la voce."""

    def __init__(self, ports: ProfilePorts) -> None:
        self._registry = ports.registry
        self._folders = ports.folders

    def __call__(self, profile_id: ProfileId) -> None:
        """Cancella la cartella del profilo e lo toglie dall'elenco.

        La conferma con cartella e contenuto la chiede l'interfaccia prima di chiamare.

        Raises:
            ProfileNotRegisteredError: id assente.
            ProfileInUseError: il profilo è aperto (anche da questa istanza): chiudilo prima.
            NotAProfileFolderError: la cartella non ha il database di un profilo; la voce
                resta (si può togliere dall'elenco).
            FolderHasOtherContentError: nella cartella c'è altro oltre ai dati del profilo;
                non si cancella nulla e la voce resta.
            FolderUnavailableError: cancellazione non riuscita o link nella cartella.
        """
        entry = next((e for e in self._registry.list_entries() if e.profile_id == profile_id), None)
        if entry is None:
            raise ProfileNotRegisteredError(str(profile_id))
        self._folders.delete_folder(entry.folder)
        self._registry.remove(profile_id)


__all__ = [
    "CreateProfile",
    "DeleteProfileFolder",
    "FolderWarning",
    "ListProfiles",
    "NewProfileRequest",
    "OpenProfileFromFolder",
    "OpenRegisteredProfile",
    "PeekProfile",
    "ProfileMismatchError",
    "ProfilePorts",
    "ProfileSession",
    "RemoveProfileFromList",
    "warnings_for",
]
