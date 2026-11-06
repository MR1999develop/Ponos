# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Tetti dell'import di file, cartelle e archivi ZIP (PLAN.md 2.11, 4.4, 4.9): un solo posto.

I valori di default sono quelli del progetto; i test e gli adattatori possono passarne di più
piccoli. L'interfaccia li legge da qui per i suoi messaggi.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

from ponos_coach.activities.domain import MAX_ACTIVITY_FILE_BYTES

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Tetti dell'esame e dell'estrazione; tutti positivi.

    I primi tre (`max_activity_files`, `max_examined_entries`, `max_depth`) fermano l'esame
    di una sorgente prima di importare qualsiasi cosa. `max_file_bytes` salta il singolo file
    (o la singola voce di uno ZIP). I tetti `max_zip_*` rifiutano l'archivio o la voce
    prima di estrarre.
    """

    max_activity_files: int = 50_000  # file .fit e .gpx, comprese le voci degli ZIP
    max_examined_entries: int = 500_000  # file e cartelle di ogni tipo, voci degli ZIP comprese
    max_depth: int = 32  # livelli di sottocartelle
    max_file_bytes: int = MAX_ACTIVITY_FILE_BYTES
    max_path_chars: int = 2048  # percorso relativo mostrato: oltre, il file si segnala e si salta
    max_zip_entries: int = 50_000  # voci di un solo ZIP
    max_zip_total_bytes: int = 5 * _GIB  # somma delle dimensioni dichiarate di uno ZIP
    max_zip_ratio: int = 100  # dimensione dichiarata / dimensione compressa, per voce
    max_zip_directory_bytes: int = 32 * _MIB  # directory centrale dello ZIP, letta in memoria

    def __post_init__(self) -> None:
        for item in fields(self):
            if getattr(self, item.name) <= 0:
                raise ValueError(f"il limite {item.name} deve essere positivo")
