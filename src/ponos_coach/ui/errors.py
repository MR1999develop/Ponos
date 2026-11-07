# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""User messages: the only place where an exception or a reason becomes text.

Never the exception's text nor a path: only what happened and how to fix it, in
plain language.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.ports import (
    ImportIssue,
    ImportLimit,
    ImportLimitExceededError,
    ImportSourceUnreadableError,
)
from ponos_coach.load.domain import MissingPhysiologyError
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.nutrition.ports import GoalPeriodNotFoundError, GoalPeriodOverlapError
from ponos_coach.nutrition.use_cases import LevelDateInFutureError
from ponos_coach.profiles.ports import (
    FolderHasOtherContentError,
    FolderNotEmptyError,
    FolderUnavailableError,
    NotAProfileFolderError,
    ProfileCorruptError,
    ProfileFormatTooNewError,
    ProfileInUseError,
    ProfileNotFoundError,
    ProfileRegistryError,
)
from ponos_coach.profiles.use_cases import ProfileMismatchError

# Contexts "Errors", "ImportIssues" and "Exclusions": lupdate only finds context and text written
# as literals, so no aliases of `QCoreApplication.translate`.


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    """What happened and how to fix it, in plain language."""

    title: str
    what: str
    remedy: str


class RegistryProblem(Enum):
    """How the profile list failed, if the Composition Root can tell."""

    TOO_NEW = "too_new"
    BUSY = "busy"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ErrorContext:
    """What an error text asks of the Composition Root.

    `registry_problem` recognises the three causes of an unusable profile list: their
    types live in the adapter, which the ui cannot import. `config_folder` is where the file
    of the list lives (an app folder, not user data).
    """

    registry_problem: Callable[[BaseException], RegistryProblem | None] = lambda _error: None
    config_folder: Path | None = None


def _in_use(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The profile is already open"),
        QCoreApplication.translate(
            "Errors", "This profile is open in another window of Ponos Coach."
        ),
        QCoreApplication.translate("Errors", "Close it there, then try again."),
    )


def _not_empty(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The folder is not empty"),
        QCoreApplication.translate(
            "Errors", "A new profile needs a folder that does not exist yet or is empty."
        ),
        QCoreApplication.translate("Errors", "Choose another folder, or a new name."),
    )


def _other_content(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The folder was not deleted"),
        QCoreApplication.translate(
            "Errors", "The folder contains files that do not belong to the profile."
        ),
        QCoreApplication.translate(
            "Errors",
            "Nothing was deleted. Move or delete those files yourself, then delete the "
            "folder again, or remove the profile from the list.",
        ),
    )


def _unavailable(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The folder cannot be used"),
        QCoreApplication.translate(
            "Errors", "Ponos Coach cannot read or write the folder of the profile."
        ),
        QCoreApplication.translate(
            "Errors", "Check that the folder exists and that you can change its contents."
        ),
    )


def _not_a_profile(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "No profile found in the folder"),
        QCoreApplication.translate("Errors", "The folder does not contain a Ponos Coach profile."),
        QCoreApplication.translate(
            "Errors",
            "If the folder was moved, choose its new place with Open profile from folder. "
            "Otherwise remove the profile from the list.",
        ),
    )


def _too_new(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The profile is from a newer version"),
        QCoreApplication.translate(
            "Errors", "This profile was saved by a newer version of Ponos Coach."
        ),
        QCoreApplication.translate("Errors", "Update Ponos Coach to open it."),
    )


def _corrupt(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The profile cannot be opened"),
        QCoreApplication.translate("Errors", "The profile data is damaged or incomplete."),
        QCoreApplication.translate(
            "Errors", "Restore the folder from a backup, or create a new profile."
        ),
    )


def _mismatch(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Another profile is in the folder"),
        QCoreApplication.translate(
            "Errors", "The folder of this profile now contains a different profile."
        ),
        QCoreApplication.translate(
            "Errors", "Remove the entry from the list and open the folder again."
        ),
    )


def _registry(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The list of profiles cannot be used"),
        QCoreApplication.translate(
            "Errors", "Ponos Coach could not read or update its list of profiles."
        ),
        QCoreApplication.translate(
            "Errors", "Try again. If the problem persists, use Help > Open the log folder."
        ),
    )


def _registry_too_new(_context: ErrorContext) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The list of profiles is from a newer version"),
        QCoreApplication.translate(
            "Errors", "The list of profiles was saved by a newer version of Ponos Coach."
        ),
        QCoreApplication.translate("Errors", "Update Ponos Coach, then open it again."),
    )


def _registry_busy(_context: ErrorContext) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The list of profiles is busy"),
        QCoreApplication.translate(
            "Errors", "Another window of Ponos Coach is updating the list of profiles."
        ),
        QCoreApplication.translate("Errors", "Try again in a few seconds."),
    )


def _registry_corrupt(context: ErrorContext) -> ErrorMessage:
    what = QCoreApplication.translate("Errors", "The file with the list of profiles is damaged.")
    if context.config_folder is not None:
        # {folder}: the configuration folder of Ponos Coach, where profiles.json is.
        what += " " + QCoreApplication.translate(
            "Errors", "It is the file profiles.json in this folder: {folder}"
        ).format(folder=context.config_folder)
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The list of profiles cannot be read"),
        what,
        QCoreApplication.translate(
            "Errors",
            "Close Ponos Coach, move profiles.json out of that folder and start again. "
            "Your profile data is not in that file: open each profile folder with "
            "File > Open profile from folder.",
        ),
    )


_REGISTRY_BUILDERS: dict[RegistryProblem, Callable[[ErrorContext], ErrorMessage]] = {
    RegistryProblem.TOO_NEW: _registry_too_new,
    RegistryProblem.BUSY: _registry_busy,
    RegistryProblem.CORRUPT: _registry_corrupt,
}


def unexpected_error_message() -> ErrorMessage:
    """Generic message of the last-resort handler (the detail is in the log)."""
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Something unexpected happened"),
        QCoreApplication.translate(
            "Errors", "Ponos Coach hit a problem it did not expect. It was written to the log."
        ),
        QCoreApplication.translate(
            "Errors",
            "If something looks wrong, close Ponos Coach and open it again. To see the log, "
            "use Help > Open the log folder.",
        ),
    )


def _import_limit(error: ImportLimitExceededError) -> ErrorMessage:
    title = QCoreApplication.translate("Errors", "The folder is too large to import at once")
    remedy = QCoreApplication.translate(
        "Errors", "Nothing was imported. Choose a subfolder and import it."
    )
    match error.limit:
        case ImportLimit.ACTIVITY_FILES:
            what = QCoreApplication.translate(
                "Errors",
                "The folder has too many activity files: the limit is %n.",
                "",
                error.maximum,
            )
        case ImportLimit.EXAMINED_ENTRIES:
            what = QCoreApplication.translate(
                "Errors",
                "The folder has too many files and folders: the limit is %n in total.",
                "",
                error.maximum,
            )
        case ImportLimit.DEPTH:
            what = QCoreApplication.translate(
                "Errors",
                "The folder has too many levels of subfolders: the limit is %n.",
                "",
                error.maximum,
            )
    return ErrorMessage(title, what, remedy)


def _import_unreadable(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The files could not be read"),
        QCoreApplication.translate("Errors", "Ponos Coach could not list the chosen files."),
        QCoreApplication.translate(
            "Errors", "Check that the folder exists and that you can open it, then try again."
        ),
    )


def _missing_physiology(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Heart-rate settings are missing"),
        QCoreApplication.translate(
            "Errors", "The profile has activities but no heart-rate settings."
        ),
        QCoreApplication.translate(
            "Errors", "Restore the profile from a backup, or create a new profile."
        ),
    )


def _period_overlap(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The goal periods overlap"),
        QCoreApplication.translate(
            "Errors", "The period shares at least one day with another goal period."
        ),
        QCoreApplication.translate("Errors", "Choose dates that no other period uses."),
    )


def _period_missing(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The goal period no longer exists"),
        QCoreApplication.translate("Errors", "The period was changed or removed meanwhile."),
        QCoreApplication.translate("Errors", "Look at the table again and repeat the change."),
    )


def _level_in_future(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "The date is in the future"),
        QCoreApplication.translate(
            "Errors", "An activity level can only hold from today or from a past day."
        ),
        QCoreApplication.translate("Errors", "Choose today or an earlier day."),
    )


def _generic(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Something went wrong"),
        QCoreApplication.translate("Errors", "The operation could not be completed."),
        QCoreApplication.translate(
            "Errors", "Try again. If the problem persists, use Help > Open the log folder."
        ),
    )


# From most specific to most general: the first match wins.
_BUILDERS: tuple[tuple[type[BaseException], Callable[[BaseException], ErrorMessage]], ...] = (
    (ProfileInUseError, _in_use),
    (FolderNotEmptyError, _not_empty),
    (FolderHasOtherContentError, _other_content),  # before its parent class
    (FolderUnavailableError, _unavailable),
    (NotAProfileFolderError, _not_a_profile),
    (ProfileNotFoundError, _not_a_profile),
    (ProfileFormatTooNewError, _too_new),
    (ProfileCorruptError, _corrupt),
    (ProfileMismatchError, _mismatch),
    (ProfileRegistryError, _registry),
    (ImportSourceUnreadableError, _import_unreadable),
    (MissingPhysiologyError, _missing_physiology),
    (GoalPeriodOverlapError, _period_overlap),
    (GoalPeriodNotFoundError, _period_missing),
    (LevelDateInFutureError, _level_in_future),
)


def describe_error(error: BaseException, context: ErrorContext | None = None) -> ErrorMessage:
    """User message for an exception; never the exception's text."""
    if isinstance(error, ImportLimitExceededError):
        return _import_limit(error)
    context = context or ErrorContext()
    problem = context.registry_problem(error)
    if problem is not None:
        return _REGISTRY_BUILDERS[problem](context)
    for kind, build in _BUILDERS:
        if isinstance(error, kind):
            return build(error)
    return _generic(error)


def import_issue_text(issue: ImportIssue) -> str:
    """Why a file was not imported, in one short sentence in plain words."""
    texts = {
        ImportIssue.EMPTY: QCoreApplication.translate("ImportIssues", "The file is empty."),
        ImportIssue.TOO_LARGE: QCoreApplication.translate("ImportIssues", "The file is too large."),
        ImportIssue.CORRUPT: QCoreApplication.translate(
            "ImportIssues", "The file is damaged or not an activity."
        ),
        ImportIssue.NOT_AN_ACTIVITY: QCoreApplication.translate(
            "ImportIssues", "The file does not contain an activity."
        ),
        ImportIssue.UNSUPPORTED_FORMAT: QCoreApplication.translate(
            "ImportIssues", "This file format is not supported."
        ),
        ImportIssue.EXTENSION_MISMATCH: QCoreApplication.translate(
            "ImportIssues", "The file content does not match its extension."
        ),
        ImportIssue.TOO_MANY_RECORDS: QCoreApplication.translate(
            "ImportIssues", "The activity has too many data points."
        ),
        ImportIssue.TOO_LONG: QCoreApplication.translate(
            "ImportIssues", "The activity lasts too long."
        ),
        ImportIssue.UNREADABLE: QCoreApplication.translate(
            "ImportIssues", "The file could not be read."
        ),
        ImportIssue.NOT_REGULAR_FILE: QCoreApplication.translate(
            "ImportIssues", "This is a link or a special file, not an ordinary file."
        ),
        ImportIssue.RESERVED_NAME: QCoreApplication.translate(
            "ImportIssues", "The file name is not allowed on Windows."
        ),
        ImportIssue.PATH_TOO_LONG: QCoreApplication.translate(
            "ImportIssues", "The file path is too long."
        ),
        ImportIssue.UNSAFE_NAME: QCoreApplication.translate(
            "ImportIssues", "The name inside the archive is not safe."
        ),
        ImportIssue.ARCHIVE_INVALID: QCoreApplication.translate(
            "ImportIssues", "The ZIP archive is damaged."
        ),
        ImportIssue.ARCHIVE_LIMIT: QCoreApplication.translate(
            "ImportIssues", "The ZIP archive is too large or too full."
        ),
        ImportIssue.ENCRYPTED: QCoreApplication.translate(
            "ImportIssues", "The file is protected by a password."
        ),
        ImportIssue.NESTED_ARCHIVE: QCoreApplication.translate(
            "ImportIssues", "A ZIP archive inside a ZIP archive."
        ),
        ImportIssue.STORAGE_FAILED: QCoreApplication.translate(
            "ImportIssues", "The file could not be saved in the profile folder."
        ),
    }
    return texts[issue]


def unavailable_detail_message(reason: ExclusionReason) -> ErrorMessage:
    """Why an activity's detail cannot be shown and what to do."""
    reimport = QCoreApplication.translate(
        "Exclusions", "Import the original file again to see its detail."
    )
    messages = {
        ExclusionReason.FILE_MISSING: (
            QCoreApplication.translate(
                "Exclusions", "The file of this activity is no longer in the archive."
            ),
            reimport,
        ),
        ExclusionReason.FILE_ALTERED: (
            QCoreApplication.translate(
                "Exclusions",
                "The file of this activity changed after it was saved, so it cannot be trusted.",
            ),
            reimport,
        ),
        ExclusionReason.FILE_TOO_LARGE: (
            QCoreApplication.translate(
                "Exclusions", "The file of this activity is larger than Ponos Coach can read."
            ),
            QCoreApplication.translate("Exclusions", "This file cannot be opened."),
        ),
        ExclusionReason.UNREADABLE: (
            QCoreApplication.translate(
                "Exclusions", "Ponos Coach could not read the file of this activity."
            ),
            QCoreApplication.translate(
                "Exclusions", "Check that you can read the profile folder, then try again."
            ),
        ),
        ExclusionReason.DECODE_FAILED: (
            QCoreApplication.translate(
                "Exclusions", "The file of this activity is no longer a valid activity file."
            ),
            reimport,
        ),
    }
    what, remedy = messages[reason]
    return ErrorMessage(
        QCoreApplication.translate("Exclusions", "The activity detail is not available"),
        what,
        remedy,
    )


def exclusion_reason_text(reason: ExclusionReason) -> str:
    """Why an activity is not in the overview, in one short sentence."""
    texts = {
        ExclusionReason.FILE_MISSING: QCoreApplication.translate(
            "Exclusions", "file missing from the archive"
        ),
        ExclusionReason.FILE_ALTERED: QCoreApplication.translate(
            "Exclusions", "file changed after it was saved"
        ),
        ExclusionReason.FILE_TOO_LARGE: QCoreApplication.translate("Exclusions", "file too large"),
        ExclusionReason.UNREADABLE: QCoreApplication.translate(
            "Exclusions", "file could not be read"
        ),
        ExclusionReason.DECODE_FAILED: QCoreApplication.translate(
            "Exclusions", "file no longer valid"
        ),
    }
    return texts[reason]
