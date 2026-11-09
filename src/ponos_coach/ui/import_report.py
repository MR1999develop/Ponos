# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Import summary for the view: counts per outcome and files not imported with the reason.

Pure function from the use case summary: the view chooses the texts.
"""

from __future__ import annotations

from dataclasses import dataclass

from ponos_coach.activities.ports import ImportIssue
from ponos_coach.activities.use_cases import ImportStatus, ImportSummary


@dataclass(frozen=True, slots=True)
class NotImported:
    """A file that did not become an activity, and why."""

    name: str
    issue: ImportIssue


@dataclass(frozen=True, slots=True)
class ImportReport:
    """Counts per outcome and files not imported, in summary order.

    `already_present` also counts activities that already arrived in another format (same
    activity, not imported twice). `cancelled` says the import stopped before the
    end: unprocessed files do not appear. `repaired` counts already present activities whose
    archived file was missing or altered and which the imported original repaired.
    """

    imported: int
    already_present: int
    ignored: int
    failed: int
    cancelled: bool
    not_imported: tuple[NotImported, ...]
    repaired: int = 0

    @property
    def examined(self) -> int:
        """Files processed in all."""
        return self.imported + self.already_present + self.repaired + self.ignored + self.failed

    @classmethod
    def from_summary(cls, summary: ImportSummary) -> ImportReport:
        """Summary for the view."""
        problems = tuple(
            NotImported(result.name, result.issue)
            for result in summary.results
            if result.issue is not None
        )
        return cls(
            imported=summary.count(ImportStatus.IMPORTED),
            already_present=summary.count(ImportStatus.ALREADY_PRESENT),
            ignored=summary.count(ImportStatus.IGNORED),
            failed=summary.count(ImportStatus.FAILED),
            cancelled=summary.cancelled,
            not_imported=problems,
            repaired=summary.count(ImportStatus.REPAIRED),
        )
