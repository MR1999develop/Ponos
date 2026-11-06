# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Costruttori di valori di dominio per i test (attività, record, parametri, profili)."""

from __future__ import annotations

import hashlib
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityOrigin,
    ActivityRecord,
    ActivitySummary,
    ArchivedFile,
    FileId,
    FitIdentity,
    GpxIdentity,
    Sport,
    format_of,
)
from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName, RegistryEntry

T0 = datetime(2026, 3, 2, 7, 0, tzinfo=UTC)
IMPORTED_AT = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def fit_identity(number: int = 1) -> FitIdentity:
    """Identità FIT distinta per ogni `number` (numero di serie)."""
    return FitIdentity(FileId(255, 1, number, T0))


def gpx_identity(number: int = 1) -> GpxIdentity:
    """Identità GPX distinta per ogni `number` (SHA-256 di un testo)."""
    return GpxIdentity(hashlib.sha256(f"gpx-{number}".encode()).hexdigest())


def archived_file(identity: ActivityIdentity) -> ArchivedFile:
    """Riferimento d'archivio plausibile per l'identità (non c'è nessun file reale)."""
    digest = hashlib.sha256(f"file-{identity}".encode()).hexdigest()
    extension = format_of(identity).value
    return ArchivedFile(PurePosixPath("2026") / f"{digest}.{extension}", digest, 1234)


def make_record(
    identity: ActivityIdentity | None = None,
    *,
    start: datetime = T0,
    elapsed_s: float = 3600.0,
    timer_s: float | None = None,
    sport: Sport = Sport.RUNNING,
    summary: ActivitySummary | None = None,
    origin: ActivityOrigin = ActivityOrigin.IMPORT,
    garmin_activity_id: int | None = None,
) -> ActivityRecord:
    """Record dell'indice con valori di default; ogni argomento si può cambiare."""
    identity = identity or fit_identity()
    return ActivityRecord(
        identity=identity,
        file_format=format_of(identity),
        sport=sport,
        start=start,
        elapsed_s=elapsed_s,
        timer_s=elapsed_s if timer_s is None else timer_s,
        summary=summary or ActivitySummary(distance_m=10_000.0),
        archived=archived_file(identity),
        origin=origin,
        imported_at=IMPORTED_AT,
        garmin_activity_id=garmin_activity_id,
    )


def physiology(threshold_bpm: int = 170) -> Physiology:
    """Parametri di esempio: FC 50-190, soglia variabile, uomo."""
    return Physiology(HeartRateLimits(50, 190), ThresholdHeartRate(threshold_bpm), Sex.MALE)


def physiology_entry(valid_from: date, threshold_bpm: int = 170) -> PhysiologyEntry:
    """Voce dei parametri fisiologici valida da una data."""
    return PhysiologyEntry(valid_from, physiology(threshold_bpm))


def absolute_path(*parts: str) -> Path:
    """Percorso assoluto sotto la cartella temporanea, senza toccare il disco."""
    return Path(tempfile.gettempdir(), "ponos-test-paths", *parts)


def registry_entry(
    number: int = 1, *, last_used: datetime = IMPORTED_AT, folder: Path | None = None
) -> RegistryEntry:
    """Voce del registro con id (UUID4 fisso dal numero), nome e cartella derivati."""
    profile_id = ProfileId.parse(f"00000000-0000-4000-8000-{number:012d}")
    return RegistryEntry(
        profile_id,
        folder or absolute_path(f"profile-{number}"),
        ProfileName.parse(f"Profilo {number}"),
        last_used,
    )
