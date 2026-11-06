# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Fake in memoria per i test dell'import: sorgente, orologio, indice e archivio osservabili."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path, PurePosixPath

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityRecord,
    ArchivedFile,
    FileFormat,
)
from ponos_coach.activities.ports import (
    ActivityIndex,
    ImportEntry,
    ImportEntryError,
    ImportIssue,
    ImportLimitExceededError,
    ImportScan,
    ImportScanCancelledError,
    SkippedEntry,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from tests.builders import IMPORTED_AT
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.profile_storage import InMemoryProfileStorage


class InMemoryImportSource:
    """Sorgente di `ImportSource` con file in un dizionario.

    `failing` associa il nome di un file all'errore di lettura; `skipped` sono i file che la
    sorgente dichiara saltati; `scan_error` fa fallire l'esame (tetto superato); `cancel_scan` lo
    fa annullare.
    """

    def __init__(
        self,
        files: Mapping[str, bytes],
        *,
        failing: Mapping[str, ImportIssue] | None = None,
        skipped: Sequence[SkippedEntry] = (),
        scan_error: ImportLimitExceededError | None = None,
        cancel_scan: bool = False,
    ) -> None:
        self._files = dict(files)
        self._failing = dict(failing or {})
        self._skipped = tuple(skipped)
        self._scan_error = scan_error
        self._cancel_scan = cancel_scan
        self.scan_cancellation: Callable[[], bool] | None = None
        self._names: dict[int, str] = {}
        self.reads: list[str] = []
        self.closed = False

    def scan(self, is_cancelled: Callable[[], bool] | None = None) -> ImportScan:
        self.scan_cancellation = is_cancelled  # per vedere che il caso d'uso lo passa
        if self._cancel_scan:
            raise ImportScanCancelledError
        if self._scan_error is not None:
            raise self._scan_error
        names = sorted(self._files)
        self._names = dict(enumerate(names))
        entries = tuple(ImportEntry(i, n, len(self._files[n])) for i, n in self._names.items())
        return ImportScan(entries, self._skipped, len(entries))

    def read(self, entry: ImportEntry, max_bytes: int) -> bytes:
        name = self._names[entry.entry_id]
        self.reads.append(name)
        if name in self._failing:
            raise ImportEntryError(self._failing[name])
        data = self._files[name]
        if len(data) > max_bytes:
            raise ImportEntryError(ImportIssue.TOO_LARGE)
        return data

    def close(self) -> None:
        self.closed = True


class FixedClock:
    """Orologio fermo."""

    def __init__(self, moment: datetime = IMPORTED_AT) -> None:
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


def new_index() -> ActivityIndex:
    """Indice delle attività di un profilo finto, vuoto."""
    profile = InMemoryProfileStorage().create(
        Path("profilo-di-prova"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Prova"),
        "UTC",
    )
    return profile.activities


class OrderLog:
    """Registro condiviso delle operazioni di scrittura, nell'ordine in cui avvengono."""

    def __init__(self) -> None:
        self.events: list[str] = []


class ObservedArchive:
    """Archivio che registra `store` e, a richiesta, fallisce (errore a scelta)."""

    def __init__(
        self, inner: InMemoryActivityArchive, log: OrderLog, fail_with: Exception | None = None
    ) -> None:
        self._inner = inner
        self._log = log
        self._fail_with = fail_with

    def store(self, content: bytes, file_format: FileFormat, start: datetime) -> ArchivedFile:
        self._log.events.append("store")
        if self._fail_with is not None:
            raise self._fail_with
        return self._inner.store(content, file_format, start)

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        return self._inner.read(file, max_bytes)

    def verify(self, file: ArchivedFile) -> None:
        self._inner.verify(file)

    def list_orphan_temporaries(self) -> Sequence[PurePosixPath]:
        return self._inner.list_orphan_temporaries()

    def remove_orphan_temporaries(self) -> int:
        return self._inner.remove_orphan_temporaries()


class ObservedIndex:
    """Indice che registra `add` e il contesto della transazione; `add` può fallire."""

    def __init__(
        self, inner: ActivityIndex, log: OrderLog, fail_add_with: Exception | None = None
    ) -> None:
        self._inner = inner
        self._log = log
        self._fail_add_with = fail_add_with

    def transaction(self) -> AbstractContextManager[None]:
        return _Logged(self._inner.transaction(), self._log)

    def exists(self, identity: ActivityIdentity) -> bool:
        return self._inner.exists(identity)

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        return self._inner.get(identity)

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        return self._inner.find_possible_duplicates(start, elapsed_s)

    def add(self, record: ActivityRecord) -> None:
        self._log.events.append("add")
        if self._fail_add_with is not None:
            raise self._fail_add_with
        self._inner.add(record)

    def list_by_start(self) -> Sequence[ActivityRecord]:
        return self._inner.list_by_start()


class _Logged(AbstractContextManager[None]):
    def __init__(self, inner: AbstractContextManager[None], log: OrderLog) -> None:
        self._inner = inner
        self._log = log

    def __enter__(self) -> None:
        self._log.events.append("begin")
        self._inner.__enter__()

    def __exit__(self, *exc: object) -> bool | None:
        outcome = "rollback" if exc[0] is not None else "commit"
        self._log.events.append(outcome)
        return self._inner.__exit__(*exc)  # type: ignore[arg-type]


def archived_names(archive: InMemoryActivityArchive) -> Iterator[str]:
    """Percorsi dell'archivio finto, come testo."""
    return (str(path) for path in sorted(archive.files))
