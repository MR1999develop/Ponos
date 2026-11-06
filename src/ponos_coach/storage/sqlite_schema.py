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

Colonne di v2 (sincronizzazione Garmin, tutte `STRICT`):

- `sync_journal`: una riga per attività remota (`garmin_id` chiave primaria) con il riepilogo
  remoto (inizio in millisecondi UTC, durata, chiave dello sport, FC nota), lo stato su insieme
  chiuso, i tentativi falliti e la causa; i CHECK impongono le stesse coerenze di
  `JournalEntry`. Nessuna chiave esterna: una voce abbinata o scaricata sopravvive alla
  cancellazione dell'attività.
- `sync_cursor`: il punto raggiunto in ciascun elenco remoto (`full`, `verify`, `incremental`).
- `sync_state`: una sola riga (`id = 1`) con la fine dell'ultima sincronizzazione.

Colonne di v3 (dati personali, pesi degli sport e zone di FC):

- `profile_body`: una sola riga (`id = 1`) con altezza (cm) e data di nascita, fisse: un trigger
  rifiuta ogni `UPDATE` e ogni `DELETE`; la data di nascita è una data ISO esistente
  (`date(col) IS col`). Assente nei profili creati prima della v3 (da completare una volta).
- `weight`: serie del peso con data di validità (data ISO esistente, non anteriore al
  1900-01-01), in decimi di chilogrammo interi.
- `sport_weights`: solo i pesi degli sport diversi dal default.
- `load_cache`: cinque colonne `zone1_s ... zone5_s` con i secondi in ciascuna zona. La cache è
  derivata e rigenerabile: la migrazione la svuota, così nessun carico resta senza zone.
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

# Insiemi chiusi della v2 (valori stabili dei dati salvati); congelati qui apposta.
_SYNC_STATUSES = "'pending','downloaded','matched','retryable','failed'"
_RETRYABLE_FAILURES = "'transient_error','rate_limited'"
_FAILURES = (
    _RETRYABLE_FAILURES + ",'unsupported_format','corrupt_file','file_too_large',"
    "'not_an_activity','archive_invalid','remote_rejected','attempts_exhausted'"
)
_LISTING_KINDS = "'full','verify','incremental'"

V2_SYNC_STATUSES: Final = frozenset(_SYNC_STATUSES.replace("'", "").split(","))
V2_FAILURES: Final = frozenset(_FAILURES.replace("'", "").split(","))
V2_RETRYABLE_FAILURES: Final = frozenset(_RETRYABLE_FAILURES.replace("'", "").split(","))
V2_LISTING_KINDS: Final = frozenset(_LISTING_KINDS.replace("'", "").split(","))

# Limiti dei dati personali (gli stessi dei tipi di dominio), congelati qui apposta.
V3_HEIGHT_CM = (100, 250)
V3_WEIGHT_TENTHS_KG = (200, 3000)
V3_MIN_BIRTH_DATE = "1900-01-01"
V3_MIN_MEASUREMENT_DATE = "1900-01-01"
V3_SPORT_WEIGHT_MAX = 5.0
ZONE_COLUMNS: Final = ("zone1_s", "zone2_s", "zone3_s", "zone4_s", "zone5_s")

# Le tabelle che ogni database di profilo ha dalla v1: senza, il file non è di un profilo.
V1_TABLES: Final = frozenset({"meta", "activities", "physiology", "load_cache"})
V2_TABLES: Final = frozenset({"sync_journal", "sync_cursor", "sync_state"})
V3_TABLES: Final = frozenset({"profile_body", "weight", "sport_weights"})

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

_V2_SYNC_JOURNAL = f"""
CREATE TABLE sync_journal (
    garmin_id       INTEGER NOT NULL PRIMARY KEY CHECK (garmin_id > 0),
    start_ms        INTEGER NOT NULL,
    duration_s      REAL    NOT NULL CHECK (duration_s > 0),
    sport_key       TEXT    NOT NULL CHECK (length(sport_key) BETWEEN 1 AND 64),
    has_heart_rate  INTEGER CHECK (has_heart_rate IS NULL OR has_heart_rate IN (0, 1)),
    status          TEXT    NOT NULL CHECK (status IN ({_SYNC_STATUSES})),
    failed_attempts INTEGER NOT NULL CHECK (failed_attempts >= 0),
    failure         TEXT    CHECK (failure IS NULL OR failure IN ({_FAILURES})),
    updated_ms      INTEGER NOT NULL,
    CHECK ((status IN ('retryable', 'failed')) = (failure IS NOT NULL)),
    CHECK (status <> 'pending' OR failed_attempts = 0),
    CHECK (status NOT IN ('retryable', 'failed') OR failed_attempts >= 1),
    CHECK (status <> 'retryable' OR failure IN ({_RETRYABLE_FAILURES})),
    CHECK (status <> 'failed' OR failure NOT IN ({_RETRYABLE_FAILURES}))
) STRICT
"""

# Indice parziale: solo le voci da lavorare, nell'ordine in cui si lavorano.
_V2_SYNC_JOURNAL_TO_DO_INDEX = (
    "CREATE INDEX sync_journal_to_do ON sync_journal (start_ms DESC, garmin_id DESC) "
    "WHERE status IN ('pending', 'retryable')"
)

_V2_SYNC_CURSOR = f"""
CREATE TABLE sync_cursor (
    kind           TEXT    NOT NULL PRIMARY KEY CHECK (kind IN ({_LISTING_KINDS})),
    next_offset    INTEGER NOT NULL CHECK (next_offset >= 0),
    declared_total INTEGER CHECK (declared_total IS NULL OR declared_total >= 0),
    completed      INTEGER NOT NULL CHECK (completed IN (0, 1))
) STRICT
"""

_V2_SYNC_STATE = """
CREATE TABLE sync_state (
    id                INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    last_completed_ms INTEGER NOT NULL
) STRICT
"""

_V3_PROFILE_BODY = f"""
CREATE TABLE profile_body (
    id         INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    height_cm  INTEGER NOT NULL CHECK (height_cm BETWEEN {V3_HEIGHT_CM[0]} AND {V3_HEIGHT_CM[1]}),
    birth_date TEXT    NOT NULL CHECK (date(birth_date) IS birth_date
                                       AND birth_date >= '{V3_MIN_BIRTH_DATE}')
) STRICT
"""

# Altezza e data di nascita sono fisse: nemmeno un UPDATE o un DELETE diretto le cambia.
_V3_PROFILE_BODY_FIXED = """
CREATE TRIGGER profile_body_is_fixed BEFORE UPDATE ON profile_body
BEGIN
    SELECT RAISE(ABORT, 'profile_body is fixed');
END
"""

_V3_PROFILE_BODY_KEPT = """
CREATE TRIGGER profile_body_is_kept BEFORE DELETE ON profile_body
BEGIN
    SELECT RAISE(ABORT, 'profile_body is fixed');
END
"""

_V3_WEIGHT = f"""
CREATE TABLE weight (
    valid_from TEXT    NOT NULL PRIMARY KEY CHECK (date(valid_from) IS valid_from
                                               AND valid_from >= '{V3_MIN_MEASUREMENT_DATE}'),
    tenths_kg  INTEGER NOT NULL CHECK (tenths_kg BETWEEN {V3_WEIGHT_TENTHS_KG[0]}
                                                     AND {V3_WEIGHT_TENTHS_KG[1]})
) STRICT
"""

_V3_SPORT_WEIGHTS = f"""
CREATE TABLE sport_weights (
    sport  TEXT NOT NULL PRIMARY KEY CHECK (sport IN ({_SPORTS})),
    weight REAL NOT NULL CHECK (weight >= 0 AND weight <= {V3_SPORT_WEIGHT_MAX})
) STRICT
"""

# I carichi salvati non hanno le zone: la cache è derivata, si svuota e si ricalcola dai file.
_V3_CLEAR_LOAD_CACHE = "DELETE FROM load_cache"
_V3_ZONE_COLUMNS = tuple(
    f"ALTER TABLE load_cache ADD COLUMN {column} REAL NOT NULL DEFAULT 0 CHECK ({column} >= 0)"
    for column in ZONE_COLUMNS
)

# Migrazione i (indice i-1): porta lo schema dalla versione i-1 alla i.
MIGRATIONS: Final[tuple[tuple[str, ...], ...]] = (
    (_V1_META, _V1_ACTIVITIES, _V1_ACTIVITIES_START_INDEX, _V1_PHYSIOLOGY, _V1_LOAD_CACHE),
    (_V2_SYNC_JOURNAL, _V2_SYNC_JOURNAL_TO_DO_INDEX, _V2_SYNC_CURSOR, _V2_SYNC_STATE),
    (
        _V3_PROFILE_BODY,
        _V3_PROFILE_BODY_FIXED,
        _V3_PROFILE_BODY_KEPT,
        _V3_WEIGHT,
        _V3_SPORT_WEIGHTS,
        _V3_CLEAR_LOAD_CACHE,
        *_V3_ZONE_COLUMNS,
    ),
)

SCHEMA_VERSION: Final = len(MIGRATIONS)
