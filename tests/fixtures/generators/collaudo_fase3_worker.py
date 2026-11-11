# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Phase 3 test process: opens a profile and dies at the k-th migration statement.

Usage: `python -I collaudo_fase3_worker.py <profile-folder> <k>`. The process opens the database
with the usual migration, but at the `k`-th statement (from 1, over the whole chain of migrations
to run) it terminates abruptly with `os._exit(77)`, without closing or rolling back anything: it is
the death of the process in the middle of the migration, like a power-off. If `k` exceeds the
statements to run the migration finishes and the process exits with 0.
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
    """Migrates the profile in `folder`, dying at statement `kill_at`."""
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
