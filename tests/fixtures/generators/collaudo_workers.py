# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Processi di prova del collaudo, da avviare con `python -I collaudo_workers.py <comando> ...`.

Servono ai test che uccidono davvero un processo o ne fanno correre due insieme. Parlano con
il test per righe di testo: `ready <pid>` quando sono pronti, poi (dove serve) attendono una
riga sullo standard input come segnale di partenza.

- `import <cartella-profilo> <cartella-sorgente> <punto>`: apre il profilo, prende il lock e
  importa la cartella con l'archivio vero. `<punto>` dice dove fermarsi e attendere la morte:
  `none` (mai: stampa `progress <n>` dopo ogni file e `finished`), `op:<n>` (prima della n-esima
  operazione di scrittura dell'archivio), `before_add:<n>` (dopo l'archivio, prima del record
  della n-esima attivita), `after_add:<n>` (record scritto ma transazione non confermata).
- `create <config> <genitore> <prefisso> <quanti>`: crea `quanti` profili sotto `genitore`.
- `race <config> <cartella>`: tenta di creare un profilo proprio nella cartella indicata.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path

from ponos_coach.activities.domain import ActivityIdentity, ActivityRecord
from ponos_coach.activities.ports import ActivityIndex
from ponos_coach.activities.use_cases import import_activities
from ponos_coach.load.domain import HeartRateLimits, Physiology, Sex, ThresholdHeartRate
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.platform.profile_registry import JsonProfileRegistry
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR, FolderKind, ProfileId, ProfileName
from ponos_coach.profiles.use_cases import CreateProfile, NewProfileRequest, ProfilePorts
from ponos_coach.storage.archive_files import OsWriteOps
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.import_scan import FolderImportSource
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage

_FIXED_NOW = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


def _say(*words: object) -> None:
    print(*words, flush=True)  # noqa: T201 - il dialogo col test e su stdout


def _wait_for_death() -> None:
    """Resta fermo finche il test non uccide il processo; se il test chiude, esce comunque."""
    sys.stdin.read()
    os._exit(3)


class _Point:
    """Dove fermarsi: `tipo:n` oppure `none`."""

    def __init__(self, spec: str) -> None:
        kind, _, number = spec.partition(":")
        self.kind = kind
        self.number = int(number) if number else 0


class _CountingOps(OsWriteOps):
    """Scritture vere, ma ferme prima della n-esima."""

    def __init__(self, point: _Point) -> None:
        self._point = point
        self._count = 0

    def _tick(self, name: str) -> None:
        self._count += 1
        if self._point.kind == "op" and self._count == self._point.number:
            _say("blocked", name)
            _wait_for_death()

    def make_dir(self, path: Path) -> bool:
        self._tick("make_dir")
        return super().make_dir(path)

    def create_exclusive(self, path: Path) -> int:
        self._tick("create_exclusive")
        return super().create_exclusive(path)

    def write(self, descriptor: int, data: memoryview) -> int:
        self._tick("write")
        return super().write(descriptor, data)

    def fsync(self, descriptor: int) -> None:
        self._tick("fsync")
        super().fsync(descriptor)

    def close(self, descriptor: int) -> None:
        self._tick("close")
        super().close(descriptor)

    def replace(self, source: Path, destination: Path) -> None:
        self._tick("replace")
        super().replace(source, destination)

    def sync_dir(self, path: Path) -> None:
        self._tick("sync_dir")
        super().sync_dir(path)


class _BlockingIndex:
    """Indice vero che si ferma attorno alla n-esima aggiunta."""

    def __init__(self, inner: ActivityIndex, point: _Point) -> None:
        self._inner = inner
        self._point = point
        self._adds = 0

    def transaction(self) -> AbstractContextManager[None]:
        return self._inner.transaction()

    def exists(self, identity: ActivityIdentity) -> bool:
        return self._inner.exists(identity)

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        return self._inner.get(identity)

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        return self._inner.find_possible_duplicates(start, elapsed_s)

    def add(self, record: ActivityRecord) -> None:
        self._adds += 1
        hit = self._adds == self._point.number
        if hit and self._point.kind == "before_add":
            _say("blocked", "before_add")
            _wait_for_death()
        self._inner.add(record)
        if hit and self._point.kind == "after_add":
            _say("blocked", "after_add")
            _wait_for_death()

    def list_by_start(self) -> Sequence[ActivityRecord]:
        return self._inner.list_by_start()


class _Clock:
    def now(self) -> datetime:
        return _FIXED_NOW


def _import(profile_folder: str, source_folder: str, spec: str) -> None:
    point = _Point(spec)
    folder = Path(profile_folder)
    lock = LocalProfileFolders(lambda _folder: FolderKind.LOCAL).acquire_lock(folder)
    opened = SqliteProfileStorage().open(folder)
    archive = FileArchive(folder / PROFILE_ARCHIVE_DIR, _CountingOps(point))
    index = _BlockingIndex(opened.activities, point)
    done = 0

    def report(finished: int, _total: int) -> None:
        nonlocal done
        done = finished
        if finished:
            _say("progress", finished)

    _say("ready", os.getpid())
    import_activities(
        FolderImportSource(Path(source_folder)), archive, index, _Clock(), report=report
    )
    _say("finished", done)
    opened.close()
    lock.release()


def _ports(config: str) -> ProfilePorts:
    return ProfilePorts(
        JsonProfileRegistry(Path(config)),
        LocalProfileFolders(lambda _folder: FolderKind.LOCAL),
        SqliteProfileStorage(),
    )


def _creator(config: str) -> CreateProfile:
    return CreateProfile(_ports(config), lambda: _FIXED_NOW, lambda: ProfileId(uuid.uuid4()))


def _request(folder: Path, name: str) -> NewProfileRequest:
    physiology = Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(170), Sex.MALE)
    return NewProfileRequest(
        ProfileName.parse(name), folder, "Europe/Rome", physiology, date(2026, 7, 1)
    )


def _try_create(create: CreateProfile, request: NewProfileRequest) -> str:
    try:
        create(request).close()
    except Exception as error:  # confine del processo di prova: l'esito va al test
        return f"error {type(error).__name__}"
    return "ok"


def _create_many(config: str, parent: str, prefix: str, count: int) -> None:
    create = _creator(config)
    _say("ready", os.getpid())
    sys.stdin.readline()  # segnale di partenza
    for number in range(count):
        folder = Path(parent) / "nuovi" / f"{prefix}-{number}"
        _say(_try_create(create, _request(folder, f"Atleta {prefix} {number}")))
    _say("finished")


def _race(config: str, folder: str) -> None:
    create = _creator(config)
    _say("ready", os.getpid())
    sys.stdin.readline()  # segnale di partenza
    _say(_try_create(create, _request(Path(folder), f"Atleta {os.getpid()}")))
    _say("finished")


def main(argv: Sequence[str]) -> int:
    command, *args = argv
    if command == "import":
        _import(args[0], args[1], args[2])
    elif command == "create":
        _create_many(args[0], args[1], args[2], int(args[3]))
    elif command == "race":
        _race(args[0], args[1])
    else:
        print(f"collaudo_workers: comando sconosciuto: {command}", file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
