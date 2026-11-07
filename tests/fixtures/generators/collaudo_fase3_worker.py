# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Processo di prova della fase 3: apre un profilo e muore alla k-esima istruzione di migrazione.

Uso: `python -I collaudo_fase3_worker.py <cartella-profilo> <k>`. Il processo apre il database
con la migrazione di sempre, ma alla `k`-esima istruzione (da 1, su tutta la catena di migrazioni
da eseguire) termina di colpo con `os._exit(77)`, senza chiudere né annullare nulla: è la morte
del processo a metà della migrazione, come uno spegnimento. Se `k` supera le istruzioni da
eseguire la migrazione finisce e il processo esce con 0.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ponos_coach.storage import sqlite_database
from ponos_coach.storage.sqlite_database import SqliteDatabase
from ponos_coach.storage.sqlite_profile import SqliteProfileStorage
from ponos_coach.storage.sqlite_schema import MIGRATIONS, SCHEMA_VERSION

DIED = 77


def main(folder: Path, kill_at: int) -> int:
    """Migra il profilo in `folder` morendo all'istruzione `kill_at`."""
    counter = 0

    def migrate(db: SqliteDatabase, from_version: int, initial_meta: Mapping[str, str]) -> None:
        nonlocal counter
        connection = db.connection
        with db.transaction():
            for statements in MIGRATIONS[from_version:]:
                for statement in statements:
                    counter += 1
                    if counter == kill_at:
                        os._exit(DIED)
                    connection.execute(statement)
            connection.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)", sorted(initial_meta.items())
            )
            connection.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")

    sqlite_database._migrate = migrate
    SqliteProfileStorage().open(folder).close()
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), int(sys.argv[2])))
