# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Nomi dei file e delle cartelle dentro la cartella di un profilo (una sola definizione)."""

PROFILE_DATABASE_NAME = "ponos.sqlite3"
PROFILE_LOCK_NAME = "ponos.lock"
PROFILE_ARCHIVE_DIR = "archivio"
PROFILE_BACKUP_DIR = "backup"

# Gli unici nomi che l'app mette nella cartella di un profilo: il database con i file che SQLite
# gli affianca, il lock, l'archivio e le copie di sicurezza. Cancellare una cartella di profilo
# è permesso solo se contiene soltanto questi nomi.
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
