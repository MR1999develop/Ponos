# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""SQLite schema of the profile and versioned migrations.

The schema version lives in `PRAGMA user_version` (single source). Migrations are
lists of statements, one at a time, executed in a single transaction by the caller: never
`executescript`, which commits on its own. An already released migration is not modified; to
change the schema the next one is added (and a test compares the closed sets of
v1 with the domain enums, so a new value forces writing a migration).

Columns of v1 (all `STRICT`, times in whole UTC milliseconds):

- `meta(key, value)`: `profile_id`, `name`, `timezone`. Unknown keys are preserved.
- `activities`: one row per activity; `identity_key` and `garmin_activity_id` UNIQUE; durations and
  distances in seconds and meters (`REAL`, measured values); path, SHA-256 and bytes of the
  original file; CHECK constraints equal to the domain invariants.
- `physiology`: parameters valid from `valid_from` (ISO date, primary key).
- `load_cache`: one load per activity (foreign key with `ON DELETE CASCADE`), with the fingerprint
  of the parameters; derived and regenerable.

Columns of v2 (Garmin synchronization, all `STRICT`):

- `sync_journal`: one row per remote activity (`garmin_id` primary key) with the remote
  summary (start in UTC milliseconds, duration, sport key, known HR), the state on a closed
  set, the failed attempts and the cause; the CHECKs impose the same consistency as
  `JournalEntry`. No foreign key: a matched or downloaded entry survives the
  deletion of the activity.
- `sync_cursor`: the point reached in each remote list (`full`, `verify`, `incremental`).
- `sync_state`: a single row (`id = 1`) with the end of the last synchronization.

Columns of v3 (personal data, sport weights and HR zones):

- `profile_body`: a single row (`id = 1`) with height (cm) and date of birth, fixed: a trigger
  rejects every `UPDATE` and every `DELETE`; the date of birth is an existing ISO date
  (`date(col) IS col`). Absent in profiles created before v3 (to be completed once).
- `weight`: weight series with validity date (existing ISO date, not earlier than
  1900-01-01), in whole tenths of a kilogram.
- `sport_weights`: only the sport weights different from the default.
- `load_cache`: five columns `zone1_s ... zone5_s` with the seconds in each zone. The cache is
  derived and regenerable: the migration empties it, so no load remains without zones.

Columns of v4 (nutrition, all `STRICT`):

- `activities.calories_kcal`: total energy declared by the device for the activity (FIT
  `session.total_calories`), NULL when absent: it is then estimated when needed. Activities
  indexed before v4 have NULL (the index is derived from the files; a re-read fills it).
- `activity_level`: level of activity outside sport with validity date (existing ISO date, not
  earlier than 1900-01-01), on a closed set.
- `meal_times`: a single row (`id = 1`) with the typical breakfast, lunch and dinner times in
  minutes after midnight, in increasing order at least two hours apart; absent means the defaults.
- `nutrition_goal`: goal periods (first day, last day included or NULL for open-ended) on a closed
  set of kinds; weight loss has a rate in grams per week and the others none; two triggers reject
  an insertion, and an update of the dates, that overlaps another period.

Columns of v5 (threshold test, `STRICT`):

- `threshold_test`: the history of the applied 30-minute threshold tests, one row per activity
  (`activity_key` is the identity key). No foreign key to `activities`: the index is derived and
  regenerable, and the history of the tests must survive a rebuild of it. Local day (existing ISO
  date, not earlier than 1900-01-01), start of the segment in UTC milliseconds, threshold HR, pace
  in whole seconds per kilometre or NULL (no distance), version of the method that computed them.

Columns of v6 (goals, availability, session types and the plan, all `STRICT`):

- `goal`: races and form goals in one table (`kind` on a closed set). A race has priority, sport,
  distance and an optional target time, a form goal has a CTL, and the CHECKs keep the two shapes
  apart and within the domain limits. A partial unique index allows one race per day (the plan
  refuses two); form goals may share a day.
- `weekly_availability`: a single row (`id = 1`), minutes per weekday (Monday = `day0_min`), the
  weekday of the long session and the weekly maximum; absent means "not set".
- `session_choice`: one row per sport and session type chosen (closed sets).
- `plan_state`: a single row (`id = 1`) with the last result of the plan engine, either a plan
  (`kind = 'plan'`: method version, canonical JSON and its SHA-256; the JSON also holds the
  parameters and the inputs that produced it) or a refusal (`kind = 'refusal'`: reason and goal
  ids). Derived and replaceable; read back as input (size, checksum, content).
- `plan_past_day`: the days of the past as they were planned, one row per day (canonical JSON of
  the `PlanDay` and its SHA-256, and the date of the plan it came from, always before the day). Two
  triggers reject every `UPDATE` and every `DELETE`: the past stays history.

Columns of v7 (threshold test protocol and measured lean mass):

- `threshold_test.protocol`: how the test was run, on a closed set (`thirty_minutes`,
  `twenty_minutes`); the tests saved before v7 were all of 30 minutes (the default).
- `lean_mass`: the measured lean mass series (`STRICT`): validity date (existing ISO date, not
  earlier than 1900-01-01, primary key) and whole tenths of a kilogram within the domain limits.
  The plausibility against the weight of the day is a rule of the use case, not of the schema.

Columns of v8 (track box for the map):

- `activities.track_south`, `track_west`, `track_north`, `track_east`: the box of the indexed track
  (start point and simplified polyline), `REAL`, all `NULL` or all present; `west > east` means the
  box crosses the antimeridian. Filled when an activity is added and, for the activities already
  indexed, by a resumable pass that reads only the index (never the archived files). Derived and
  rebuildable: `NULL` for an activity without positions, and until the pass reaches the row.
"""

from __future__ import annotations

from typing import Final

from ponos_coach.storage.schema_workout_backup import WORKOUT_BACKUP_MIGRATION

# Closed sets of v1 (stable values of the saved data); frozen here on purpose.
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

# Closed sets of v2 (stable values of the saved data); frozen here on purpose.
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

# Limits of the personal data (the same as the domain types), frozen here on purpose.
V3_HEIGHT_CM = (100, 250)
V3_WEIGHT_TENTHS_KG = (200, 3000)
V3_MIN_BIRTH_DATE = "1900-01-01"
V3_MIN_MEASUREMENT_DATE = "1900-01-01"
V3_SPORT_WEIGHT_MAX = 5.0
# Limits of the nutrition data (the same as the domain types), frozen here on purpose.
V4_MAX_ACTIVITY_KCAL = 50_000
V4_MEAL_MINUTES = (0, 1439)
V4_MIN_MEAL_GAP_MIN = 120
V4_WEEKLY_LOSS_G = (100, 1000)
# Limits of the threshold test data (the same as the domain types), frozen here on purpose.
V5_THRESHOLD_BPM = (40, 250)
V5_PACE_S_PER_KM = (90, 3600)
# Limits of the goals and plan data (the same as the domain types), frozen here on purpose.
V6_DISTANCE_M = (400, 500_000)
V6_TARGET_TIME_S = (60, 7 * 24 * 3600)
V6_MAX_CTL = 200
V6_DAY_MINUTES = (0, 720)
V6_WEEK_MINUTES = (0, 6000)
V6_MAX_PLAN_JSON_CHARS = 16 * 1024 * 1024
V6_MAX_DAY_JSON_CHARS = 64 * 1024
V6_MAX_REFUSAL_IDS_CHARS = 65_536
# Limits of the lean mass data (the same as the domain type), frozen here on purpose.
V7_LEAN_MASS_TENTHS_KG = (150, 2000)
_THRESHOLD_PROTOCOLS = "'thirty_minutes','twenty_minutes'"
V7_THRESHOLD_PROTOCOLS: Final = frozenset(_THRESHOLD_PROTOCOLS.replace("'", "").split(","))
ZONE_COLUMNS: Final = ("zone1_s", "zone2_s", "zone3_s", "zone4_s", "zone5_s")

_GOAL_KINDS_V6 = "'race','fitness'"
_PRIORITIES = "'A','B','C'"
_SESSION_TYPES = "'easy','long','medium','threshold','vo2max','recovery','strength','alternative'"
_PLAN_KINDS = "'plan','refusal'"
_REFUSAL_REASONS = (
    "'no_goals','goal_not_in_future','goals_on_same_day','no_availability','no_session_types'"
)

V6_GOAL_KINDS: Final = frozenset(_GOAL_KINDS_V6.replace("'", "").split(","))
V6_PRIORITIES: Final = frozenset(_PRIORITIES.replace("'", "").split(","))
V6_SESSION_TYPES: Final = frozenset(_SESSION_TYPES.replace("'", "").split(","))
V6_PLAN_KINDS: Final = frozenset(_PLAN_KINDS.replace("'", "").split(","))
V6_REFUSAL_REASONS: Final = frozenset(_REFUSAL_REASONS.replace("'", "").split(","))

_ACTIVITY_LEVELS = "'sedentary','light','active','very_active'"
_GOAL_KINDS = "'weight_loss','maintenance','performance'"

V4_ACTIVITY_LEVELS: Final = frozenset(_ACTIVITY_LEVELS.replace("'", "").split(","))
V4_GOAL_KINDS: Final = frozenset(_GOAL_KINDS.replace("'", "").split(","))

# The tables that every profile database has since v1: without them, the file is not a profile's.
V1_TABLES: Final = frozenset({"meta", "activities", "physiology", "load_cache"})
V2_TABLES: Final = frozenset({"sync_journal", "sync_cursor", "sync_state"})
V3_TABLES: Final = frozenset({"profile_body", "weight", "sport_weights"})
V4_TABLES: Final = frozenset({"activity_level", "meal_times", "nutrition_goal"})
V5_TABLES: Final = frozenset({"threshold_test"})
V6_TABLES: Final = frozenset(
    {"goal", "weekly_availability", "session_choice", "plan_state", "plan_past_day"}
)
V7_TABLES: Final = frozenset({"lean_mass"})

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

# Partial index: only the entries to process, in the order in which they are processed.
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

# Height and date of birth are fixed: not even a direct UPDATE or DELETE changes them.
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

# Saved loads have no zones: the cache is derived, it is emptied and recomputed from the files.
_V3_CLEAR_LOAD_CACHE = "DELETE FROM load_cache"
_V3_ZONE_COLUMNS = tuple(
    f"ALTER TABLE load_cache ADD COLUMN {column} REAL NOT NULL DEFAULT 0 CHECK ({column} >= 0)"
    for column in ZONE_COLUMNS
)

_V4_CALORIES = (
    "ALTER TABLE activities ADD COLUMN calories_kcal REAL "
    "CHECK (calories_kcal IS NULL OR "
    f"(calories_kcal > 0 AND calories_kcal <= {V4_MAX_ACTIVITY_KCAL}))"
)

_V4_ACTIVITY_LEVEL = f"""
CREATE TABLE activity_level (
    valid_from TEXT NOT NULL PRIMARY KEY CHECK (date(valid_from) IS valid_from
                                               AND valid_from >= '{V3_MIN_MEASUREMENT_DATE}'),
    level      TEXT NOT NULL CHECK (level IN ({_ACTIVITY_LEVELS}))
) STRICT
"""

_V4_MEAL_TIMES = f"""
CREATE TABLE meal_times (
    id            INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    breakfast_min INTEGER NOT NULL CHECK (breakfast_min BETWEEN {V4_MEAL_MINUTES[0]}
                                                            AND {V4_MEAL_MINUTES[1]}),
    lunch_min     INTEGER NOT NULL CHECK (lunch_min BETWEEN {V4_MEAL_MINUTES[0]}
                                                        AND {V4_MEAL_MINUTES[1]}),
    dinner_min    INTEGER NOT NULL CHECK (dinner_min BETWEEN {V4_MEAL_MINUTES[0]}
                                                         AND {V4_MEAL_MINUTES[1]}),
    CHECK (lunch_min - breakfast_min >= {V4_MIN_MEAL_GAP_MIN}),
    CHECK (dinner_min - lunch_min >= {V4_MIN_MEAL_GAP_MIN})
) STRICT
"""

_V4_NUTRITION_GOAL = f"""
CREATE TABLE nutrition_goal (
    start_date    TEXT    NOT NULL PRIMARY KEY CHECK (date(start_date) IS start_date
                                                  AND start_date >= '{V3_MIN_MEASUREMENT_DATE}'),
    end_date      TEXT    CHECK (end_date IS NULL OR (date(end_date) IS end_date
                                                      AND end_date >= start_date)),
    kind          TEXT    NOT NULL CHECK (kind IN ({_GOAL_KINDS})),
    weekly_loss_g INTEGER CHECK (weekly_loss_g IS NULL
                                 OR weekly_loss_g BETWEEN {V4_WEEKLY_LOSS_G[0]}
                                                      AND {V4_WEEKLY_LOSS_G[1]}),
    CHECK ((kind = 'weight_loss') = (weekly_loss_g IS NOT NULL))
) STRICT
"""

# Periods never overlap, not even with a direct INSERT (open end = no end date).
_V4_NUTRITION_GOAL_NO_OVERLAP = """
CREATE TRIGGER nutrition_goal_no_overlap BEFORE INSERT ON nutrition_goal
WHEN EXISTS (
    SELECT 1 FROM nutrition_goal AS g
    WHERE g.start_date <= COALESCE(NEW.end_date, '9999-12-31')
      AND COALESCE(g.end_date, '9999-12-31') >= NEW.start_date
)
BEGIN
    SELECT RAISE(ABORT, 'nutrition goal periods overlap');
END
"""

# The same when a period is moved by an UPDATE: it is compared with every OTHER row.
_V4_NUTRITION_GOAL_NO_OVERLAP_ON_UPDATE = """
CREATE TRIGGER nutrition_goal_no_overlap_on_update BEFORE UPDATE ON nutrition_goal
WHEN EXISTS (
    SELECT 1 FROM nutrition_goal AS g
    WHERE g.start_date <> OLD.start_date
      AND g.start_date <= COALESCE(NEW.end_date, '9999-12-31')
      AND COALESCE(g.end_date, '9999-12-31') >= NEW.start_date
)
BEGIN
    SELECT RAISE(ABORT, 'nutrition goal periods overlap');
END
"""

_V5_THRESHOLD_TEST = f"""
CREATE TABLE threshold_test (
    activity_key     TEXT    NOT NULL PRIMARY KEY
                             CHECK (length(activity_key) BETWEEN 5 AND 128
                                    AND substr(activity_key, 1, 4) IN ('fit:', 'gpx:')),
    test_date        TEXT    NOT NULL CHECK (date(test_date) IS test_date
                                             AND test_date >= '{V3_MIN_MEASUREMENT_DATE}'),
    segment_start_ms INTEGER NOT NULL,
    threshold_bpm    INTEGER NOT NULL CHECK (threshold_bpm BETWEEN {V5_THRESHOLD_BPM[0]}
                                                               AND {V5_THRESHOLD_BPM[1]}),
    pace_s_per_km    INTEGER CHECK (pace_s_per_km IS NULL
                                    OR pace_s_per_km BETWEEN {V5_PACE_S_PER_KM[0]}
                                                         AND {V5_PACE_S_PER_KM[1]}),
    method_version   INTEGER NOT NULL CHECK (method_version >= 1)
) STRICT
"""

_V6_GOAL = f"""
CREATE TABLE goal (
    goal_id       TEXT    NOT NULL PRIMARY KEY
                          CHECK (length(goal_id) BETWEEN 1 AND 64
                                 AND goal_id NOT GLOB '*[^A-Za-z0-9_-]*'),
    kind          TEXT    NOT NULL CHECK (kind IN ({_GOAL_KINDS_V6})),
    goal_date     TEXT    NOT NULL CHECK (date(goal_date) IS goal_date
                                          AND goal_date >= '{V3_MIN_MEASUREMENT_DATE}'),
    priority      TEXT    CHECK (priority IS NULL OR priority IN ({_PRIORITIES})),
    sport         TEXT    CHECK (sport IS NULL OR sport IN ({_SPORTS})),
    distance_m    INTEGER CHECK (distance_m IS NULL
                                 OR distance_m BETWEEN {V6_DISTANCE_M[0]} AND {V6_DISTANCE_M[1]}),
    target_time_s INTEGER CHECK (target_time_s IS NULL
                                 OR target_time_s BETWEEN {V6_TARGET_TIME_S[0]}
                                                      AND {V6_TARGET_TIME_S[1]}),
    ctl           REAL    CHECK (ctl IS NULL OR (ctl >= 0 AND ctl <= {V6_MAX_CTL})),
    CHECK (kind <> 'race' OR (priority IS NOT NULL AND sport IS NOT NULL
                              AND distance_m IS NOT NULL AND ctl IS NULL)),
    CHECK (kind <> 'fitness' OR (ctl IS NOT NULL AND priority IS NULL AND sport IS NULL
                                 AND distance_m IS NULL AND target_time_s IS NULL))
) STRICT
"""

# One race a day, even with a direct INSERT or UPDATE (the plan refuses two races on one day).
_V6_GOAL_ONE_RACE_PER_DAY = (
    "CREATE UNIQUE INDEX goal_one_race_per_day ON goal (goal_date) WHERE kind = 'race'"
)
_V6_GOAL_BY_DATE = "CREATE INDEX goal_by_date ON goal (goal_date, goal_id)"

_V6_DAY_COLUMNS = ",\n    ".join(
    f"day{i}_min INTEGER NOT NULL "
    f"CHECK (day{i}_min BETWEEN {V6_DAY_MINUTES[0]} AND {V6_DAY_MINUTES[1]})"
    for i in range(7)
)
_V6_WEEKLY_AVAILABILITY = f"""
CREATE TABLE weekly_availability (
    id           INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    {_V6_DAY_COLUMNS},
    long_day     INTEGER NOT NULL CHECK (long_day BETWEEN 0 AND 6),
    max_week_min INTEGER NOT NULL CHECK (max_week_min BETWEEN {V6_WEEK_MINUTES[0]}
                                                          AND {V6_WEEK_MINUTES[1]})
) STRICT
"""

_V6_SESSION_CHOICE = f"""
CREATE TABLE session_choice (
    sport        TEXT NOT NULL CHECK (sport IN ({_SPORTS})),
    session_type TEXT NOT NULL CHECK (session_type IN ({_SESSION_TYPES})),
    PRIMARY KEY (sport, session_type)
) STRICT
"""

_V6_PLAN_STATE = f"""
CREATE TABLE plan_state (
    id               INTEGER NOT NULL PRIMARY KEY CHECK (id = 1),
    kind             TEXT    NOT NULL CHECK (kind IN ({_PLAN_KINDS})),
    generated_on     TEXT    NOT NULL CHECK (date(generated_on) IS generated_on
                                             AND generated_on >= '{V3_MIN_MEASUREMENT_DATE}'),
    method_version   INTEGER CHECK (method_version IS NULL OR method_version >= 1),
    plan_json        TEXT    CHECK (plan_json IS NULL
                                    OR length(plan_json) BETWEEN 2 AND {V6_MAX_PLAN_JSON_CHARS}),
    plan_sha256      TEXT    CHECK (plan_sha256 IS NULL OR length(plan_sha256) = 64),
    refusal_reason   TEXT    CHECK (refusal_reason IS NULL
                                    OR refusal_reason IN ({_REFUSAL_REASONS})),
    refusal_goal_ids TEXT    CHECK (refusal_goal_ids IS NULL
                                    OR length(refusal_goal_ids) <= {V6_MAX_REFUSAL_IDS_CHARS}),
    CHECK (kind <> 'plan' OR (method_version IS NOT NULL AND plan_json IS NOT NULL
                              AND plan_sha256 IS NOT NULL AND refusal_reason IS NULL
                              AND refusal_goal_ids IS NULL)),
    CHECK (kind <> 'refusal' OR (refusal_reason IS NOT NULL AND refusal_goal_ids IS NOT NULL
                                 AND method_version IS NULL AND plan_json IS NULL
                                 AND plan_sha256 IS NULL))
) STRICT
"""

_V6_PLAN_PAST_DAY = f"""
CREATE TABLE plan_past_day (
    day        TEXT NOT NULL PRIMARY KEY CHECK (date(day) IS day
                                                AND day >= '{V3_MIN_MEASUREMENT_DATE}'),
    planned_on TEXT NOT NULL CHECK (date(planned_on) IS planned_on AND planned_on < day),
    day_json   TEXT NOT NULL CHECK (length(day_json) BETWEEN 2 AND {V6_MAX_DAY_JSON_CHARS}),
    day_sha256 TEXT NOT NULL CHECK (length(day_sha256) = 64)
) STRICT
"""

# The past stays history: not even a direct UPDATE or DELETE changes a frozen day.
_V6_PLAN_PAST_DAY_FIXED = """
CREATE TRIGGER plan_past_day_is_fixed BEFORE UPDATE ON plan_past_day
BEGIN
    SELECT RAISE(ABORT, 'plan_past_day is history');
END
"""

_V6_PLAN_PAST_DAY_KEPT = """
CREATE TRIGGER plan_past_day_is_kept BEFORE DELETE ON plan_past_day
BEGIN
    SELECT RAISE(ABORT, 'plan_past_day is history');
END
"""

_V7_THRESHOLD_PROTOCOL = f"""
ALTER TABLE threshold_test ADD COLUMN protocol TEXT NOT NULL DEFAULT 'thirty_minutes'
    CHECK (protocol IN ({_THRESHOLD_PROTOCOLS}))
"""

_V7_LEAN_MASS = f"""
CREATE TABLE lean_mass (
    valid_from TEXT    NOT NULL PRIMARY KEY CHECK (date(valid_from) IS valid_from
                                                   AND valid_from >= '{V3_MIN_MEASUREMENT_DATE}'),
    tenths_kg  INTEGER NOT NULL CHECK (tenths_kg BETWEEN {V7_LEAN_MASS_TENTHS_KG[0]}
                                                     AND {V7_LEAN_MASS_TENTHS_KG[1]})
) STRICT
"""

_V8_TRACK_SOUTH = """
ALTER TABLE activities ADD COLUMN track_south REAL
    CHECK (track_south IS NULL OR track_south BETWEEN -90 AND 90)
"""

_V8_TRACK_WEST = """
ALTER TABLE activities ADD COLUMN track_west REAL
    CHECK (track_west IS NULL OR track_west BETWEEN -180 AND 180)
"""

_V8_TRACK_NORTH = """
ALTER TABLE activities ADD COLUMN track_north REAL
    CHECK (track_north IS NULL OR track_north BETWEEN -90 AND 90)
"""

# The last column carries the constraints that tie the four together.
_V8_TRACK_EAST = """
ALTER TABLE activities ADD COLUMN track_east REAL
    CHECK (track_east IS NULL OR (
        track_east BETWEEN -180 AND 180
        AND track_south IS NOT NULL AND track_west IS NOT NULL AND track_north IS NOT NULL
        AND track_south <= track_north))
    CHECK (track_east IS NOT NULL OR (
        track_south IS NULL AND track_west IS NULL AND track_north IS NULL))
"""

# Migration i (index i-1): brings the schema from version i-1 to i.
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
    (
        _V4_CALORIES,
        _V4_ACTIVITY_LEVEL,
        _V4_MEAL_TIMES,
        _V4_NUTRITION_GOAL,
        _V4_NUTRITION_GOAL_NO_OVERLAP,
        _V4_NUTRITION_GOAL_NO_OVERLAP_ON_UPDATE,
    ),
    (_V5_THRESHOLD_TEST,),
    (
        _V6_GOAL,
        _V6_GOAL_ONE_RACE_PER_DAY,
        _V6_GOAL_BY_DATE,
        _V6_WEEKLY_AVAILABILITY,
        _V6_SESSION_CHOICE,
        _V6_PLAN_STATE,
        _V6_PLAN_PAST_DAY,
        _V6_PLAN_PAST_DAY_FIXED,
        _V6_PLAN_PAST_DAY_KEPT,
    ),
    (_V7_THRESHOLD_PROTOCOL, _V7_LEAN_MASS),
    (_V8_TRACK_SOUTH, _V8_TRACK_WEST, _V8_TRACK_NORTH, _V8_TRACK_EAST),
    # PROVISIONAL v9 (ADR 013): renumbered when merged with the injuries migration.
    WORKOUT_BACKUP_MIGRATION,
)

SCHEMA_VERSION: Final = len(MIGRATIONS)
