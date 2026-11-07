# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Names of the files and folders inside a profile's folder (one single definition)."""

PROFILE_DATABASE_NAME = "ponos.sqlite3"
PROFILE_LOCK_NAME = "ponos.lock"
PROFILE_ARCHIVE_DIR = "archivio"
PROFILE_BACKUP_DIR = "backup"

# The only names the app puts in a profile's folder: the database with the files SQLite
# places next to it, the lock, the archive and the backups. Deleting a profile folder
# is allowed only if it contains just these names.
PROFILE_OWNED_NAMES = frozenset(
    {
        PROFILE_DATABASE_NAME,
        PROFILE_DATABASE_NAME + "-wal",
        PROFILE_DATABASE_NAME + "-shm",
        PROFILE_DATABASE_NAME + "-journal",
        PROFILE_LOCK_NAME,
        PROFILE_ARCHIVE_DIR,
        PROFILE_BACKUP_DIR,
    }
)
