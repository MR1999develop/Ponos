# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riepilogo dell'import per la vista: conteggi per esito e file non importati col motivo.

Funzione pura dal riepilogo del caso d'uso: i testi li sceglie la vista.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.activities.ports import ImportIssue
from ponos_coach.activities.use_cases import ImportStatus, ImportSummary


@dataclass(frozen=True, slots=True)
class NotImported:
    """Un file che non è diventato un'attività, e perché."""

    name: str
    issue: ImportIssue


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Conteggi per esito e file non importati, nell'ordine del riepilogo.

    `already_present` conta anche le attività già arrivate in un altro formato (stessa
    attività, non importata due volte). `cancelled` dice che l'import si è fermato prima della
    fine: i file non trattati non compaiono.
    """

    imported: int
    already_present: int
    ignored: int
    failed: int
    cancelled: bool
    not_imported: tuple[NotImported, ...]

    @property
    def examined(self) -> int:
        """File trattati in tutto."""
        return self.imported + self.already_present + self.ignored + self.failed

    @classmethod
    def from_summary(cls, summary: ImportSummary) -> ImportReport:
        """Riepilogo per la vista."""
        problems = tuple(
            NotImported(result.name, result.issue)
            for result in summary.results
            if result.issue is not None
        )
        return cls(
            imported=summary.count(ImportStatus.IMPORTED),
            already_present=summary.count(ImportStatus.ALREADY_PRESENT)
            + summary.count(ImportStatus.DUPLICATE_OF_OTHER_FORMAT),
            ignored=summary.count(ImportStatus.IGNORED),
            failed=summary.count(ImportStatus.FAILED),
            cancelled=summary.cancelled,
            not_imported=problems,
        )
