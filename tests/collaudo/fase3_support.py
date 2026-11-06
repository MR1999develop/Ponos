# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Impalcatura del collaudo della fase 3: attività miste, profili "da zero" e confronti.

Le attività sono FIT sintetici (corsa, trail, bici, forza, escursione senza FC) con segmenti di
FC che toccano tutte e cinque le zone. Un profilo "da zero" riceve gli stessi file e gli stessi
parametri e pesi finali di un profilo che li ha cambiati passo passo: la sua panoramica, calcolata
con la cache vuota, è l'oracolo del ricalcolo incrementale.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import PhysiologyEntry, SportWeight
from ponos_coach.load.use_cases import ProfileOverview
from tests.collaudo.support import RealProfile, create_profile
from tests.fixtures.generators import fit_writer as fw

STEP_S = 2  # un campione ogni 2 s: file piccoli, intervalli ben sotto i 10 s
TODAY = date(2026, 7, 1)  # lo stesso `TODAY` del collaudo (support.py)

type Segments = Sequence[tuple[int | None, int]]  # (bpm o None, secondi)

# Soglia 170: Z1 sotto 144,5, Z2 fino a 153, Z3 fino a 161,5, Z4 fino a 170, Z5 da 170 in su.
EVERY_ZONE: Segments = ((120, 300), (150, 300), (158, 300), (165, 300), (172, 300), (185, 300))
EASY: Segments = ((110, 1800),)
NO_HEART_RATE: Segments = ((None, 1800),)


@dataclass(frozen=True, slots=True)
class MixedActivity:
    """Un'attività sintetica del collaudo."""

    name: str
    start: datetime
    sport: fw.Sport
    sub_sport: fw.SubSport
    segments: Segments


MIXED: tuple[MixedActivity, ...] = (
    MixedActivity(
        "corsa", datetime(2026, 1, 12, 7, 0, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.GENERIC, EVERY_ZONE,
    ),
    MixedActivity(
        "bici", datetime(2026, 1, 14, 16, 0, tzinfo=UTC), fw.Sport.CYCLING,
        fw.SubSport.ROAD, EVERY_ZONE,
    ),
    MixedActivity(  # 23:30 del 31 gennaio, ora di Roma: giorno locale prima del cambio
        "mezzanotte", datetime(2026, 1, 31, 22, 30, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.GENERIC, EVERY_ZONE,
    ),
    MixedActivity(
        "forza", datetime(2026, 2, 1, 17, 0, tzinfo=UTC), fw.Sport.TRAINING,
        fw.SubSport.STRENGTH_TRAINING, EASY,
    ),
    MixedActivity(
        "trail", datetime(2026, 3, 5, 6, 0, tzinfo=UTC), fw.Sport.RUNNING,
        fw.SubSport.TRAIL, EVERY_ZONE,
    ),
    MixedActivity(
        "escursione", datetime(2026, 3, 20, 8, 0, tzinfo=UTC), fw.Sport.HIKING,
        fw.SubSport.GENERIC, NO_HEART_RATE,
    ),
)  # fmt: skip

CHANGE_DAYS = (
    date(2025, 12, 1),  # prima di ogni attività
    date(2026, 1, 12),  # il giorno di un'attività
    date(2026, 1, 31),  # il giorno dell'attività a cavallo
    date(2026, 2, 1),
    date(2026, 2, 15),  # in mezzo
    date(2026, 3, 20),
    date(2026, 6, 30),  # dopo ogni attività
    TODAY,  # la data della voce iniziale: sostituisce
)


def segmented_fit(serial: int, spec: MixedActivity) -> bytes:
    """FIT di un'attività con la FC a segmenti (`None`: campione senza FC)."""
    first = fw.datetime_to_fit(spec.start)
    samples: list[fw.Sample] = []
    elapsed = 0
    for bpm, seconds in spec.segments:
        for _ in range(seconds // STEP_S):
            samples.append(fw.Sample(first + elapsed, bpm))
            elapsed += STEP_S
    activity = fw.ActivitySpec(
        spec.start, spec.sport, spec.sub_sport, samples, serial_number=serial,
        total_elapsed_ms=elapsed * 1000, total_timer_ms=elapsed * 1000,
    )  # fmt: skip
    return fw.build_activity(activity)


def write_mixed(folder: Path, specs: Sequence[MixedActivity] = MIXED) -> Path:
    """Scrive le attività in `folder` (una cartella nuova) e la restituisce."""
    folder.mkdir(parents=True)
    for serial, spec in enumerate(specs, start=3_000_000):
        (folder / f"{spec.name}.fit").write_bytes(segmented_fit(serial, spec))
    return folder


def profile_with_mixed(base: Path, name: str = "primo") -> RealProfile:
    """Profilo vero (`base/name`) con le attività miste già importate."""
    profile = create_profile(base / name, folder_name="profilo")
    profile.import_folder(write_mixed(base / name / "file"))
    assert len(profile.records()) == len(MIXED)
    return profile


def scratch_overview(
    base: Path,
    name: str,
    entries: Sequence[PhysiologyEntry],
    sport_weights: Mapping[Sport, SportWeight],
) -> tuple[ProfileOverview, RealProfile]:
    """Panoramica di un profilo nuovo con gli stessi file e i parametri e pesi finali dati.

    La cache è vuota: ogni carico si calcola dal file. Il profilo resta aperto (lo chiude il
    chiamante) per confronti ulteriori, per esempio sulla panoramica di una seconda lettura.
    """
    scratch = profile_with_mixed(base, name)
    for entry in entries:
        scratch.session.physiology.put(entry)
    scratch.session.sport_weights.replace(dict(sport_weights))
    return scratch.overview(), scratch


def assert_same_overview(first: ProfileOverview, second: ProfileOverview, today: date) -> None:
    """Stesse attività, stesse escluse, stessa serie PMC e stesse aggregazioni, bit per bit."""
    assert first.activities == second.activities
    assert first.excluded == second.excluded
    assert first.pmc.first_day == second.pmc.first_day
    for series in ("atl", "ctl", "tsb"):
        assert np.array_equal(getattr(first.pmc, series), getattr(second.pmc, series))
    load_a, load_b = first.weekly_load(today), second.weekly_load(today)
    assert load_a.weeks == load_b.weeks
    assert load_a.sports == load_b.sports
    assert np.array_equal(load_a.load, load_b.load)
    zones_a, zones_b = first.weekly_zones(today), second.weekly_zones(today)
    assert zones_a.weeks == zones_b.weeks
    assert np.array_equal(zones_a.seconds, zones_b.seconds)
