# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Messaggi per l'utente: l'unico punto in cui un'eccezione o un motivo diventa testo.

Mai il testo dell'eccezione né un percorso: solo cosa è successo e come rimediare, in
linguaggio comune.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.ports import (
    ImportIssue,
    ImportLimit,
    ImportLimitExceededError,
    ImportSourceUnreadableError,
)
from ponos_coach.load.domain import MissingPhysiologyError
from ponos_coach.load.use_cases import ExclusionReason
from ponos_coach.profiles.ports import (
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

# Contesti "Errors", "ImportIssues" e "Exclusions": lupdate trova solo contesto e testo scritti
# come letterali, quindi niente alias di `QCoreApplication.translate`.


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    """Cosa è successo e come rimediare, in linguaggio comune."""

    title: str
    what: str
    remedy: str


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
            "Errors", "Try again. If the problem persists, check the application log."
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
                "Errors", "The folder has more than %n activity file(s).", "", error.maximum
            )
        case ImportLimit.EXAMINED_ENTRIES:
            what = QCoreApplication.translate(
                "Errors", "The folder has more than %n file(s) and folder(s) in total.", "",
                error.maximum,
            )  # fmt: skip
        case ImportLimit.DEPTH:
            what = QCoreApplication.translate(
                "Errors", "The folder has more than %n level(s) of subfolders.", "", error.maximum
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


def _generic(_error: BaseException) -> ErrorMessage:
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Something went wrong"),
        QCoreApplication.translate("Errors", "The operation could not be completed."),
        QCoreApplication.translate(
            "Errors", "Try again. If the problem persists, check the application log."
        ),
    )


# Dal più specifico al più generale: vale la prima corrispondenza.
_BUILDERS: tuple[tuple[type[BaseException], Callable[[BaseException], ErrorMessage]], ...] = (
    (ProfileInUseError, _in_use),
    (FolderNotEmptyError, _not_empty),
    (FolderUnavailableError, _unavailable),
    (NotAProfileFolderError, _not_a_profile),
    (ProfileNotFoundError, _not_a_profile),
    (ProfileFormatTooNewError, _too_new),
    (ProfileCorruptError, _corrupt),
    (ProfileMismatchError, _mismatch),
    (ProfileRegistryError, _registry),
    (ImportSourceUnreadableError, _import_unreadable),
    (MissingPhysiologyError, _missing_physiology),
)


def describe_error(error: BaseException) -> ErrorMessage:
    """Messaggio per l'utente di un'eccezione; mai il testo dell'eccezione."""
    if isinstance(error, ImportLimitExceededError):
        return _import_limit(error)
    for kind, build in _BUILDERS:
        if isinstance(error, kind):
            return build(error)
    return _generic(error)


def import_issue_text(issue: ImportIssue) -> str:
    """Perché un file non è stato importato, in una frase breve in parole comuni."""
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


def exclusion_reason_text(reason: ExclusionReason) -> str:
    """Perché un'attività non è nella panoramica, in una frase breve."""
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
