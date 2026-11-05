# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Elenco dei profili su file JSON nella cartella di configurazione dell'app.

Formato (versione 1):

    {"format_version": 1,
     "profiles": [{"id": "<uuid4>", "folder": "<percorso assoluto>", "name": "<nome>",
                   "last_used_utc": "<ISO 8601 con fuso UTC>"}]}

I campi che questa versione non conosce (nella radice o in una voce) si conservano al
risalvataggio. Ogni scrittura (leggi-modifica-scrivi) avviene sotto un lock del sistema
operativo su `profiles.json.lock`, accanto al file, con attesa limitata: così due istanze
dell'app non si perdono le modifiche. Un file illeggibile, troppo grande, corrotto o di una
versione futura è un errore esplicito e non si sovrascrive mai.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ponos_coach.platform.atomic_file import FileTooLargeError, read_limited, write_atomic
from ponos_coach.platform.file_lock import acquire_file_lock
from ponos_coach.profiles.domain import ProfileId, ProfileName, RegistryEntry
from ponos_coach.profiles.ports import (
    FolderUnavailableError,
    ProfileAlreadyRegisteredError,
    ProfileInUseError,
    ProfileNotRegisteredError,
    ProfileRegistryError,
)

REGISTRY_FILE_NAME = "profiles.json"
REGISTRY_FORMAT_VERSION = 1
REGISTRY_MAX_BYTES = 1024 * 1024
REGISTRY_LOCK_NAME = REGISTRY_FILE_NAME + ".lock"
REGISTRY_LOCK_TIMEOUT_S = 2.0  # attesa massima del lock di un'altra istanza
_LOCK_RETRY_INTERVAL_S = 0.05

type _Raw = dict[str, Any]


class RegistryCorruptError(ProfileRegistryError):
    """Il file dell'elenco è illeggibile, troppo grande o non ha la forma attesa."""


class RegistryFormatTooNewError(ProfileRegistryError):
    """Il file dell'elenco è di una versione più nuova di quella che questa app conosce."""


class RegistryBusyError(ProfileRegistryError):
    """Un'altra istanza tiene il lock dell'elenco oltre l'attesa massima: nulla è stato scritto."""


def _corrupt(reason: str) -> RegistryCorruptError:
    return RegistryCorruptError(f"elenco dei profili non valido: {reason}")


def _same_folder_key(folder: Path) -> str:
    return os.path.normcase(os.fspath(folder))


def _entry_from_raw(raw: object) -> RegistryEntry:
    """Voce di dominio da un oggetto JSON; ogni difetto è `RegistryCorruptError`."""
    if not isinstance(raw, dict):
        raise _corrupt("voce non è un oggetto")
    try:
        identifier, folder, name, used = (
            raw["id"],
            raw["folder"],
            raw["name"],
            raw["last_used_utc"],
        )
        if not all(isinstance(v, str) for v in (identifier, folder, name, used)):
            raise _corrupt("campo di una voce non è testo")
        return RegistryEntry(
            ProfileId.parse(identifier),
            Path(folder),
            ProfileName(name),
            datetime.fromisoformat(used),
        )
    except KeyError as exc:
        raise _corrupt("voce senza un campo obbligatorio") from exc
    except ValueError as exc:  # include InvalidProfileNameError
        raise _corrupt("voce con un valore non valido") from exc


def _raw_from_entry(entry: RegistryEntry) -> _Raw:
    return {
        "id": str(entry.profile_id),
        "folder": os.fspath(entry.folder),
        "name": entry.cached_name.value,
        "last_used_utc": entry.last_used_utc.astimezone(UTC).isoformat(),
    }


class _Document:
    """Contenuto del file: radice grezza (campi sconosciuti compresi) e voci."""

    def __init__(self, root: _Raw) -> None:
        self.root = root
        profiles = root["profiles"]
        self.raw_entries: list[_Raw] = profiles
        self.entries = [_entry_from_raw(raw) for raw in profiles]
        self._check_unique()

    def _check_unique(self) -> None:
        ids = {e.profile_id for e in self.entries}
        folders = {_same_folder_key(e.folder) for e in self.entries}
        if len(ids) != len(self.entries) or len(folders) != len(self.entries):
            raise _corrupt("voci con lo stesso id o la stessa cartella")

    def index_of(self, profile_id: ProfileId) -> int:
        for index, entry in enumerate(self.entries):
            if entry.profile_id == profile_id:
                return index
        raise ProfileNotRegisteredError(str(profile_id))


def _parse_document(data: bytes | None) -> _Document:
    if data is None:
        return _Document({"format_version": REGISTRY_FORMAT_VERSION, "profiles": []})
    try:
        root = json.loads(data)
    except (ValueError, RecursionError) as exc:  # UnicodeDecodeError è un ValueError
        raise _corrupt("non è JSON") from exc
    if not isinstance(root, dict):
        raise _corrupt("la radice non è un oggetto")
    version = root.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise _corrupt("versione del formato mancante o non valida")
    if version > REGISTRY_FORMAT_VERSION:
        raise RegistryFormatTooNewError(
            f"elenco dei profili di una versione più nuova ({version}): aggiorna l'app"
        )
    if not isinstance(root.get("profiles"), list):
        raise _corrupt("elenco dei profili mancante")
    return _Document(root)


class JsonProfileRegistry:
    """`ProfileRegistry` su `<config_dir>/profiles.json`.

    Ogni operazione rilegge il file e, se cambia qualcosa, lo riscrive in modo atomico: nessuna
    copia in memoria che possa divergere. La cartella `config_dir` si crea alla prima scrittura.
    Le scritture prendono prima il lock del sistema operativo su `profiles.json.lock`, attendendo
    al più `lock_timeout_s` secondi (poi `RegistryBusyError`); la lettura non lo prende, perché
    il file si sostituisce in modo atomico. Il file di lock resta sul disco: il lock è del
    sistema, e si libera da solo anche se l'istanza muore.
    """

    def __init__(self, config_dir: Path, lock_timeout_s: float = REGISTRY_LOCK_TIMEOUT_S) -> None:
        if lock_timeout_s <= 0:
            raise ValueError("l'attesa del lock deve essere positiva")
        self._path = config_dir / REGISTRY_FILE_NAME
        self._lock_path = config_dir / REGISTRY_LOCK_NAME
        self._lock_timeout_s = lock_timeout_s

    def list_entries(self) -> Sequence[RegistryEntry]:
        """Vedi `ProfileRegistry.list_entries`."""
        entries = self._load().entries
        return sorted(entries, key=lambda e: (-e.last_used_utc.timestamp(), str(e.profile_id)))

    def add(self, entry: RegistryEntry) -> None:
        """Vedi `ProfileRegistry.add`."""
        with self._exclusive():
            document = self._load()
            folder_key = _same_folder_key(entry.folder)
            for existing in document.entries:
                if existing.profile_id == entry.profile_id or (
                    _same_folder_key(existing.folder) == folder_key
                ):
                    raise ProfileAlreadyRegisteredError(str(entry.profile_id))
            document.raw_entries.append(_raw_from_entry(entry))
            self._save(document)

    def remove(self, profile_id: ProfileId) -> None:
        """Vedi `ProfileRegistry.remove`."""
        with self._exclusive():
            document = self._load()
            del document.raw_entries[document.index_of(profile_id)]
            self._save(document)

    def mark_used(self, profile_id: ProfileId, when_utc: datetime) -> None:
        """Vedi `ProfileRegistry.mark_used`."""
        with self._exclusive():
            document = self._load()
            index = document.index_of(profile_id)
            updated = RegistryEntry(
                profile_id,
                document.entries[index].folder,
                document.entries[index].cached_name,
                when_utc,
            )
            # Si cambia solo il campo: gli altri, anche quelli sconosciuti, restano com'erano.
            document.raw_entries[index]["last_used_utc"] = _raw_from_entry(updated)["last_used_utc"]
            self._save(document)

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        """Tiene il lock del sistema sul file accanto all'elenco, con attesa limitata."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ProfileRegistryError("elenco dei profili non scrivibile") from exc
        deadline = time.monotonic() + self._lock_timeout_s
        while True:
            try:
                lock = acquire_file_lock(self._lock_path)
                break
            except ProfileInUseError as exc:
                if time.monotonic() >= deadline:
                    raise RegistryBusyError(
                        "elenco dei profili in uso da un'altra istanza: riprova"
                    ) from exc
                time.sleep(_LOCK_RETRY_INTERVAL_S)
            except FolderUnavailableError as exc:
                raise ProfileRegistryError("lock dell'elenco dei profili non ottenibile") from exc
        try:
            yield
        finally:
            lock.release()

    def _load(self) -> _Document:
        try:
            data = read_limited(self._path, REGISTRY_MAX_BYTES)
        except FileTooLargeError as exc:
            raise _corrupt("file troppo grande") from exc
        except OSError as exc:
            raise ProfileRegistryError("elenco dei profili non leggibile") from exc
        return _parse_document(data)

    def _save(self, document: _Document) -> None:
        text = json.dumps(document.root, ensure_ascii=False, indent=2, sort_keys=False)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(self._path, (text + "\n").encode("utf-8"))
        except OSError as exc:
            raise ProfileRegistryError("elenco dei profili non scrivibile") from exc
