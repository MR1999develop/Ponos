# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riga dell'indice delle attività e riferimento al file originale nell'archivio."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PurePosixPath

from ponos_coach.activities.domain.activity import Activity
from ponos_coach.activities.domain.identity import ActivityIdentity, FileFormat, format_of
from ponos_coach.activities.domain.sport import Sport
from ponos_coach.activities.domain.summary import ActivitySummary
from ponos_coach.activities.domain.timing import check_durations, check_utc

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_ARCHIVE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_MAX_ARCHIVE_PARTS = 8


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """File originale nell'archivio: percorso relativo generato dal programma, SHA-256, byte.

    `relative_path` è relativo alla cartella dell'archivio, con `/` come separatore, fatto di
    non più di 8 componenti di caratteri `A-Za-z0-9._-` (mai `..`, assoluto o con `\\`):
    nessun dato esterno entra mai in un nome di file. `sha256_hex` ha 64 cifre minuscole.
    """

    relative_path: PurePosixPath
    sha256_hex: str
    size_bytes: int

    def __post_init__(self) -> None:
        parts = self.relative_path.parts
        if not parts or len(parts) > _MAX_ARCHIVE_PARTS or self.relative_path.is_absolute():
            raise ValueError("percorso d'archivio vuoto, troppo profondo o assoluto")
        if not all(_ARCHIVE_PART.fullmatch(p) and not p.endswith(".") for p in parts):
            raise ValueError("percorso d'archivio con componenti non ammessi")
        if not _SHA256_HEX.fullmatch(self.sha256_hex):
            raise ValueError("lo SHA-256 deve essere di 64 cifre esadecimali minuscole")
        if self.size_bytes < 0:
            raise ValueError("la dimensione non può essere negativa")


class ActivityOrigin(Enum):
    """Come l'attività è entrata nel profilo. Il valore è stabile (dati salvati)."""

    IMPORT = "import"
    GARMIN = "garmin"


@dataclass(frozen=True, slots=True)
class ActivityRecord:
    """Una riga dell'indice: ciò che serve per elencare, filtrare e ritrovare il file.

    Invarianti: `file_format` è quello dell'identità; inizio e `imported_at` in UTC; durate
    finite e positive con `timer_s <= elapsed_s`; con origine `GARMIN` l'id Garmin c'è ed è
    positivo (un'attività importata da file può riceverlo dopo, se abbinata).
    """

    identity: ActivityIdentity
    file_format: FileFormat
    sport: Sport
    start: datetime
    elapsed_s: float
    timer_s: float
    summary: ActivitySummary
    archived: ArchivedFile
    origin: ActivityOrigin
    imported_at: datetime
    garmin_activity_id: int | None = None

    def __post_init__(self) -> None:
        if format_of(self.identity) is not self.file_format:
            raise ValueError("il formato non corrisponde all'identità")
        check_utc(self.start)
        check_utc(self.imported_at)
        check_durations(self.elapsed_s, self.timer_s)
        if self.garmin_activity_id is not None and self.garmin_activity_id <= 0:
            raise ValueError("l'id Garmin deve essere positivo")
        if self.origin is ActivityOrigin.GARMIN and self.garmin_activity_id is None:
            raise ValueError("un'attività scaricata da Garmin ha il suo id Garmin")

    @classmethod
    def from_activity(
        cls,
        activity: Activity,
        archived: ArchivedFile,
        origin: ActivityOrigin,
        imported_at: datetime,
        garmin_activity_id: int | None = None,
    ) -> ActivityRecord:
        """Riga dell'indice per un'attività appena letta e archiviata."""
        return cls(
            activity.identity,
            activity.file_format,
            activity.sport,
            activity.start,
            activity.elapsed_s,
            activity.timer_s,
            activity.summary,
            archived,
            origin,
            imported_at,
            garmin_activity_id,
        )
