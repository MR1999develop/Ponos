# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Primitive di basso livello per scritture durevoli su file, senza dipendenze dal progetto.

Le usano sia `storage` (archivio, copie di sicurezza) sia `platform` (elenco dei profili), che
restano indipendenti tra loro: la regola di scrittura durevole sta in questo solo posto.
"""

from ponos_coach.fsio.durable import (
    make_subdirectory,
    replace_file,
    sync_directory,
    write_file_atomically,
)

__all__ = ["make_subdirectory", "replace_file", "sync_directory", "write_file_atomically"]
