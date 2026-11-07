# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""User-facing error texts: what, why and remedy, without the exception text."""

from __future__ import annotations

from pathlib import Path

import pytest

from ponos_coach.profiles.ports import (
    FolderHasOtherContentError,
    FolderUnavailableError,
    ProfileRegistryError,
)
from ponos_coach.ui.errors import (
    ErrorContext,
    RegistryProblem,
    describe_error,
    unexpected_error_message,
)

USER_TEXT = "Segreto_Con_Controllo_Log"


def test_a_folder_with_other_content_says_nothing_was_deleted_and_what_to_do() -> None:
    message = describe_error(FolderHasOtherContentError(USER_TEXT))

    assert message.title == "The folder was not deleted"
    assert "do not belong to the profile" in message.what
    assert message.remedy.startswith("Nothing was deleted.")
    assert USER_TEXT not in message.title + message.what + message.remedy


def test_the_generic_unavailable_folder_message_is_still_used_for_other_failures() -> None:
    assert describe_error(FolderUnavailableError("x")).title == "The folder cannot be used"


@pytest.mark.parametrize(
    ("problem", "title", "remedy"),
    [
        (RegistryProblem.TOO_NEW, "The list of profiles is from a newer version", "Update"),
        (RegistryProblem.BUSY, "The list of profiles is busy", "Try again in a few seconds."),
        (RegistryProblem.CORRUPT, "The list of profiles cannot be read", "move profiles.json"),
    ],
)
def test_each_registry_problem_has_its_own_message_with_its_remedy(
    problem: RegistryProblem, title: str, remedy: str, tmp_path: Path
) -> None:
    context = ErrorContext(lambda _error: problem, tmp_path)

    message = describe_error(ProfileRegistryError(USER_TEXT), context)

    assert message.title == title
    assert remedy in message.remedy
    assert USER_TEXT not in message.title + message.what + message.remedy


def test_a_corrupt_registry_message_says_where_the_file_is(tmp_path: Path) -> None:
    context = ErrorContext(lambda _error: RegistryProblem.CORRUPT, tmp_path)

    message = describe_error(ProfileRegistryError("x"), context)

    assert "profiles.json" in message.what
    assert str(tmp_path) in message.what
    assert "Open profile from folder" in message.remedy  # the profile data are not in the file


def test_an_unclassified_registry_error_keeps_the_generic_message_that_points_to_the_log() -> None:
    message = describe_error(ProfileRegistryError("x"))

    assert message.title == "The list of profiles cannot be used"
    assert "Help > Open the log folder" in message.remedy


def test_no_message_sends_the_user_to_a_log_without_saying_how_to_open_it() -> None:
    generic = describe_error(RuntimeError("x"))

    assert "Open the log folder" in generic.remedy
    assert "Open the log folder" in unexpected_error_message().remedy
