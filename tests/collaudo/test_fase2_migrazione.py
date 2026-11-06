# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-01 per la fase 2: dal database v1 congelato alla v2 usata davvero dalla sincronizzazione.

I test della migrazione stanno in `test_database_del_profilo.py` (schema congelato, copia di
sicurezza, campi sconosciuti). Qui si prova ciò che manca: la migrazione interrotta a metà non
lascia un database a pezzi, e un profilo v1 migrato sincronizza davvero (con il lavoro vecchio
intatto).
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import closing
from pathlib import Path

import pytest

from ponos_coach.__main__ import extract_original_activity
from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import SessionTokens, SyncStatus
from ponos_coach.activities.sync import SyncMode
from ponos_coach.garmin.account import GarminConnectAccount
from ponos_coach.profiles.domain import PROFILE_DATABASE_NAME, ProfileId
from ponos_coach.profiles.ports import ProfileStorageError
from ponos_coach.storage import sqlite_database, sqlite_schema
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.ui.sync_model import SyncFinished
from ponos_coach.ui.sync_worker import SyncServices, SyncWorker
from tests.collaudo.fase2_support import FAST, scripted_client
from tests.collaudo.test_database_del_profilo import PROFILE_ID, write_frozen_v1
from tests.fakes.notifier import RecordingNotifier
from tests.fakes.tokens import InMemoryTokenStore
from tests.fixtures.generators.sync_harness import FakeSyncClock, MidpointRandom
from tests.garmin.scripted import PLACEHOLDER_TOKENS, ScriptedFactory


def user_version(path: Path) -> int:
    with closing(sqlite3.connect(path)) as raw:
        return int(raw.execute("PRAGMA user_version").fetchone()[0])


def table_names(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as raw:
        return {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_t_dati_01_a_migration_that_fails_halfway_leaves_the_v1_database_as_it_was(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / PROFILE_DATABASE_NAME
    write_frozen_v1(path)
    tables_before = table_names(path)
    broken_v2 = (
        sqlite_schema.MIGRATIONS[1][0],  # una tabella nuova che va a buon fine...
        "CREATE TABLE questa_no (",  # ...poi un errore di sintassi
    )
    monkeypatch.setattr(sqlite_database, "MIGRATIONS", (sqlite_schema.MIGRATIONS[0], broken_v2))

    with pytest.raises((ProfileStorageError, sqlite3.Error)):
        SqliteProfileStorage().open(tmp_path)

    assert user_version(path) == 1
    assert table_names(path) == tables_before  # nessuna tabella a metà
    monkeypatch.undo()
    opened = SqliteProfileStorage().open(tmp_path)  # e il giro buono riesce
    try:
        assert opened.info.format_version == 2
        assert len(opened.activities.list_by_start()) == 1
    finally:
        opened.close()


def test_t_dati_01_a_v1_profile_syncs_after_the_migration_and_keeps_its_old_activity(
    tmp_path: Path,
) -> None:
    write_frozen_v1(tmp_path / PROFILE_DATABASE_NAME)
    profile_id = ProfileId(uuid.UUID(PROFILE_ID))
    store = InMemoryTokenStore()
    store.save(profile_id, SessionTokens(PLACEHOLDER_TOKENS))
    client = scripted_client(3)
    services = SyncServices(
        account=GarminConnectAccount(ScriptedFactory(client)),
        tokens=store,
        extract_original=extract_original_activity,
        decode=decode_stored_activity,
        clock=FakeSyncClock(),
        make_rng=MidpointRandom,
        notifier=RecordingNotifier(),
        policy=FAST,
    )
    worker = SyncWorker(services, SqliteProfileStorage(), FileArchive.in_profile_folder)

    result = worker.synchronize(
        profile_id,
        tmp_path,
        SyncMode.SYNC,
        automatic=False,
        cancelled=threading.Event(),
        report=lambda _progress: None,
    )

    assert isinstance(result, SyncFinished) and result.outcome.completed
    assert result.outcome.downloaded == 3
    assert user_version(tmp_path / PROFILE_DATABASE_NAME) == 2
    opened = SqliteProfileStorage().open(tmp_path)
    try:
        records = opened.activities.list_by_start()
        assert len(records) == 4  # la vecchia attività v1 e le tre nuove
        assert opened.sync_journal.counts()[SyncStatus.DOWNLOADED] == 3
        assert sum(1 for r in records if r.garmin_activity_id is None) == 1
    finally:
        opened.close()
    with closing(sqlite3.connect(tmp_path / PROFILE_DATABASE_NAME)) as raw:
        unknown = raw.execute("SELECT value FROM meta WHERE key = 'futuro'").fetchone()
    assert unknown == ("conservami",)  # il campo sconosciuto sopravvive anche alla sincronizzazione
