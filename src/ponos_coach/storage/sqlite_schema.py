# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Schema SQLite del profilo e migrazioni versionate.

La versione dello schema sta in `PRAGMA user_version` (unica fonte). Le migrazioni sono
elenchi di istruzioni, una per volta, eseguite in una sola transazione dal chiamante: mai
`executescript`, che conferma da solo. Una migrazione già rilasciata non si modifica; per
cambiare lo schema si aggiunge la successiva (e un test confronta gli insiemi chiusi della
v1 con gli enum del dominio, così un valore nuovo obbliga a scrivere una migrazione).

Colonne di v1 (tutte `STRICT`, tempi in millisecondi UTC interi):

- `meta(key, value)`: `profile_id`, `name`, `timezone`. Chiavi sconosciute si conservano.
- `activities`: una riga per attività; `identity_key` e `garmin_activity_id` UNIQUE; durate e
  distanze in secondi e metri (`REAL`, valori misurati); percorso, SHA-256 e byte del file
  originale; vincoli CHECK uguali agli invarianti del dominio.
- `physiology`: parametri validi da `valid_from` (data ISO, chiave primaria).
- `load_cache`: un carico per attività (chiave esterna con `ON DELETE CASCADE`), con l'impronta
  dei parametri; derivato e rigenerabile.
"""

from __future__ import annotations

from typing import Final

# Insiemi chiusi della v1 (valori stabili dei dati salvati); congelati qui apposta.
_SPORTS = "'running','trail','cycling','strength','hiking','walking','other'"
_FORMATS = "'fit','gpx'"
_ORIGINS = "'import','garmin'"
_SEXES = "'male','female'"
_LOAD_SOURCES = "'heart_rate','estimate'"

V1_SPORTS: Final = frozenset(_SPORTS.replace("'", "").split(","))
V1_FORMATS: Final = frozenset(_FORMATS.replace("'", "").split(","))
V1_ORIGINS: Final = frozenset(_ORIGINS.replace("'", "").split(","))
V1_SEXES: Final = frozenset(_SEXES.replace("'", "").split(","))
V1_LOAD_SOURCES: Final = frozenset(_LOAD_SOURCES.replace("'", "").split(","))

# Le tabelle che ogni database di profilo ha dalla v1: senza, il file non è di un profilo.
V1_TABLES: Final = frozenset({"meta", "activities", "physiology", "load_cache"})

_V1_META = """
CREATE TABLE meta (
    key   TEXT NOT NULL PRIMARY KEY CHECK (length(key) BETWEEN 1 AND 64),
    value TEXT NOT NULL CHECK (length(value) <= 1024)
) STRICT
"""

_V1_ACTIVITIES = f"""
CREATE TABLE activities (
    id                 INTEGER PRIMARY KEY,
    identity_key       TEXT    NOT NULL UNIQUE CHECK (length(identity_key) BETWEEN 5 AND 128),
    file_format        TEXT    NOT NULL CHECK (file_format IN ({_FORMATS})),
    sport              TEXT    NOT NULL CHECK (sport IN ({_SPORTS})),
    start_ms           INTEGER NOT NULL,
    elapsed_s          REAL    NOT NULL CHECK (elapsed_s > 0),
    timer_s            REAL    NOT NULL CHECK (timer_s > 0 AND timer_s <= elapsed_s),
    distance_m         REAL    CHECK (distance_m IS NULL OR distance_m >= 0),
    ascent_m           REAL    CHECK (ascent_m IS NULL OR ascent_m >= 0),
    start_lat          REAL    CHECK (start_lat IS NULL OR start_lat BETWEEN -90 AND 90),
    start_lon          REAL    CHECK (start_lon IS NULL OR start_lon BETWEEN -180 AND 180),
    track_polyline     TEXT    CHECK (track_polyline IS NULL
                                      OR length(track_polyline) BETWEEN 1 AND 24000),
    archive_path       TEXT    NOT NULL CHECK (length(archive_path) BETWEEN 1 AND 1024),
    archive_sha256     TEXT    NOT NULL CHECK (length(archive_sha256) = 64),
    archive_size_bytes INTEGER NOT NULL CHECK (archive_size_bytes >= 0),
    origin             TEXT    NOT NULL CHECK (origin IN ({_ORIGINS})),
    imported_ms        INTEGER NOT NULL,
    garmin_activity_id INTEGER UNIQUE CHECK (garmin_activity_id IS NULL OR garmin_activity_id > 0),
    CHECK ((start_lat IS NULL) = (start_lon IS NULL)),
    CHECK (origin <> 'garmin' OR garmin_activity_id IS NOT NULL),
    CHECK (substr(identity_key, 1, 4) = file_format || ':')
) STRICT
"""

_V1_ACTIVITIES_START_INDEX = (
    "CREATE INDEX activities_by_start ON activities (start_ms, identity_key)"
)

_V1_PHYSIOLOGY = f"""
CREATE TABLE physiology (
    valid_from    TEXT    NOT NULL PRIMARY KEY CHECK (length(valid_from) = 10),
    min_bpm       INTEGER NOT NULL CHECK (min_bpm >= 20),
    max_bpm       INTEGER NOT NULL CHECK (max_bpm <= 250),
    threshold_bpm INTEGER NOT NULL CHECK (threshold_bpm >= 40),
    sex           TEXT    NOT NULL CHECK (sex IN ({_SEXES})),
    CHECK (min_bpm < max_bpm),
    CHECK (threshold_bpm > min_bpm AND threshold_bpm <= max_bpm)
) STRICT
"""

_V1_LOAD_CACHE = f"""
CREATE TABLE load_cache (
    activity_id       INTEGER NOT NULL PRIMARY KEY
                              REFERENCES activities (id) ON DELETE CASCADE,
    fingerprint       TEXT    NOT NULL CHECK (length(fingerprint) = 64),
    trimp             REAL    NOT NULL,
    hr_tss            REAL    NOT NULL,
    load              REAL    NOT NULL,
    used_samples      INTEGER NOT NULL CHECK (used_samples >= 0),
    discarded_samples INTEGER NOT NULL CHECK (discarded_samples >= 0),
    excluded_seconds  REAL    NOT NULL CHECK (excluded_seconds >= 0),
    source            TEXT    NOT NULL CHECK (source IN ({_LOAD_SOURCES}))
) STRICT
"""

# Migrazione i (indice i-1): porta lo schema dalla versione i-1 alla i.
MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    (_V1_META, _V1_ACTIVITIES, _V1_ACTIVITIES_START_INDEX, _V1_PHYSIOLOGY, _V1_LOAD_CACHE),
)

SCHEMA_VERSION: Final = len(MIGRATIONS)
