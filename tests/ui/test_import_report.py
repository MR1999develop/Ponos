# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Import summary for the view and reason texts, without a window."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ponos_coach.activities.ports import ImportIssue
from ponos_coach.activities.use_cases import FileImportResult, ImportStatus, ImportSummary
from ponos_coach.ui.errors import import_issue_text
from ponos_coach.ui.import_report import ImportReport, NotImported
from tests.ui.helpers import profile_activity


def test_report_counts_each_outcome_and_lists_files_not_imported() -> None:
    identity = profile_activity(datetime(2026, 3, 2, 7, 0, tzinfo=UTC)).identity
    summary = ImportSummary(
        (
            FileImportResult("a.fit", ImportStatus.IMPORTED),
            FileImportResult("b.fit", ImportStatus.IMPORTED),
            FileImportResult("c.fit", ImportStatus.ALREADY_PRESENT, matches=identity),
            FileImportResult("d.fit", ImportStatus.IGNORED, ImportIssue.NOT_AN_ACTIVITY),
            FileImportResult("e.fit", ImportStatus.FAILED, ImportIssue.CORRUPT),
        ),
        cancelled=True,
    )

    report = ImportReport.from_summary(summary)

    assert (report.imported, report.already_present, report.ignored, report.failed) == (2, 1, 1, 1)
    assert report.cancelled
    assert report.examined == 5
    assert report.not_imported == (
        NotImported("d.fit", ImportIssue.NOT_AN_ACTIVITY),
        NotImported("e.fit", ImportIssue.CORRUPT),
    )


@pytest.mark.parametrize("issue", list(ImportIssue))
def test_every_reason_has_a_plain_sentence(issue: ImportIssue) -> None:
    text = import_issue_text(issue)

    assert text.endswith(".")
    assert "_" not in text  # never the technical identifier


def test_a_repaired_archive_file_is_counted_apart_and_not_as_a_problem() -> None:
    identity = profile_activity(datetime(2026, 3, 2, 7, 0, tzinfo=UTC)).identity
    summary = ImportSummary(
        (
            FileImportResult("a.fit", ImportStatus.REPAIRED, matches=identity),
            FileImportResult("b.fit", ImportStatus.IMPORTED),
        ),
        cancelled=False,
    )

    report = ImportReport.from_summary(summary)

    assert (report.imported, report.repaired, report.already_present) == (1, 1, 0)
    assert report.examined == 2
    assert report.not_imported == ()


def test_activities_brought_back_or_left_out_are_counted_apart_and_are_not_problems() -> None:
    identity = profile_activity(datetime(2026, 3, 2, 7, 0, tzinfo=UTC)).identity
    summary = ImportSummary(
        (
            FileImportResult("a.fit", ImportStatus.BROUGHT_BACK),
            FileImportResult("b.fit", ImportStatus.LEFT_OUT, matches=identity),
            FileImportResult("c.fit", ImportStatus.LEFT_OUT, matches=identity),
            FileImportResult("d.fit", ImportStatus.IMPORTED),
        ),
        cancelled=False,
    )

    report = ImportReport.from_summary(summary)

    assert (report.brought_back, report.left_out, report.imported) == (1, 2, 1)
    assert report.examined == 4
    assert report.not_imported == ()


def test_a_left_out_file_must_say_which_deleted_activity_it_is() -> None:
    with pytest.raises(ValueError, match="matching activity"):
        FileImportResult("a.fit", ImportStatus.LEFT_OUT)
