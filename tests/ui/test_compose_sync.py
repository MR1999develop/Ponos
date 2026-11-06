# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Composizione dell'estrattore dello ZIP scaricato: ogni causa di `zipread` diventa una causa
definitiva della voce del giornale."""

from __future__ import annotations

import pytest

from ponos_coach.__main__ import _FAILURE_BY_ZIP_REASON, extract_original_activity
from ponos_coach.activities.domain import RETRYABLE_REASONS, FailureReason, FileFormat
from ponos_coach.activities.sync import OriginalExtractionError
from ponos_coach.zipread import ZipReason
from tests.fakes.garmin import OriginalKind, original_zip


@pytest.mark.parametrize("reason", list(ZipReason))
def test_every_zip_reason_maps_to_a_definitive_failure(reason: ZipReason) -> None:
    failure = _FAILURE_BY_ZIP_REASON[reason]

    assert failure not in RETRYABLE_REASONS
    assert failure is not FailureReason.ATTEMPTS_EXHAUSTED


def test_a_garmin_download_with_a_fit_or_a_gpx_gives_the_file_and_its_format() -> None:
    fit = extract_original_activity(original_zip(1, OriginalKind.FIT))
    gpx = extract_original_activity(original_zip(1, OriginalKind.GPX))

    assert fit.file_format is FileFormat.FIT
    assert gpx.file_format is FileFormat.GPX
    assert fit.content and gpx.content


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (OriginalKind.TCX, FailureReason.UNSUPPORTED_FORMAT),
        (OriginalKind.EMPTY_ZIP, FailureReason.UNSUPPORTED_FORMAT),
        (OriginalKind.TRUNCATED_ZIP, FailureReason.ARCHIVE_INVALID),
        (OriginalKind.NOT_A_ZIP, FailureReason.ARCHIVE_INVALID),
    ],
)
def test_a_download_without_an_activity_file_fails_for_a_definitive_reason(
    kind: OriginalKind, reason: FailureReason
) -> None:
    with pytest.raises(OriginalExtractionError) as error:
        extract_original_activity(original_zip(1, kind))

    assert error.value.reason is reason
