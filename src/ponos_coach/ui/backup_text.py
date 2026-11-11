# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Texts and decisions of the profile backup and restore, without widgets.

One message per reason of failure, in plain words; never a path, a name or a content of the
backup in the text of an error.
"""

from __future__ import annotations

import re
from datetime import date

from PySide6.QtCore import QCoreApplication, QLocale

from ponos_coach.profiles.domain.backup import BACKUP_SUFFIX, BackupSummary
from ponos_coach.profiles.ports import (
    BackupFileExistsError,
    BackupInvalidError,
    BackupProblem,
    BackupTooNewError,
    BackupWriteError,
    FolderNotEmptyError,
    FolderUnavailableError,
    ProfileInUseError,
)
from ponos_coach.ui.errors import ErrorMessage

_NOT_FILE_NAME_CHARS = re.compile(r'[\/:*?"<>|\x00-\x1f]')
_MAX_NAME_CHARS = 60


def suggested_backup_name(profile_name: str, today: date) -> str:
    """`<profile name> <date>.ponosbackup`, with the characters a file name cannot hold replaced."""
    safe = _NOT_FILE_NAME_CHARS.sub("-", profile_name).strip(" .")[:_MAX_NAME_CHARS] or "Profile"
    return f"{safe} {today.isoformat()}{BACKUP_SUFFIX}"


def size_text(total_bytes: int) -> str:
    """A size in the user's locale (KiB, MiB...)."""
    return QLocale().formattedDataSize(total_bytes)


def backup_done_text(summary: BackupSummary) -> str:
    """The summary after a backup: size and number of activity files."""
    # {size}: the size of the backup, e.g. "12.3 MiB"; %n: activity files in the backup.
    return "\n".join(
        (
            QCoreApplication.translate("Backup", "Size: {size}.").format(
                size=size_text(summary.total_bytes)
            ),
            QCoreApplication.translate("Backup", "Activity files: %n.", "", summary.archive_files),
        )
    )


def restore_done_text(summary: BackupSummary) -> str:
    """The summary after a restore: size and number of activity files."""
    return backup_done_text(summary)


def backup_error_message(error: BaseException) -> ErrorMessage:
    """The message for a failed backup."""
    if isinstance(error, BackupFileExistsError):
        return ErrorMessage(
            QCoreApplication.translate("Backup", "The backup was not written"),
            QCoreApplication.translate(
                "Backup",
                "A file with this name already exists, and a backup never replaces a file.",
            ),
            QCoreApplication.translate("Backup", "Choose another name or another folder."),
        )
    if isinstance(error, ValueError):  # the destination is inside the profile folder
        return ErrorMessage(
            QCoreApplication.translate("Backup", "The backup was not written"),
            QCoreApplication.translate(
                "Backup", "The backup cannot be saved inside the folder of the profile it copies."
            ),
            QCoreApplication.translate("Backup", "Choose a folder outside the profile folder."),
        )
    return ErrorMessage(
        QCoreApplication.translate("Backup", "The backup was not written"),
        QCoreApplication.translate(
            "Backup", "The profile could not be read or the file could not be written."
        ),
        QCoreApplication.translate(
            "Backup", "Check that the folder is writable and has enough free space, then try again."
        ),
    )


def restore_error_message(error: BaseException) -> ErrorMessage:
    """The message for a failed restore: one reason each, nothing was left behind."""
    title = QCoreApplication.translate("Backup", "The profile was not restored")
    if isinstance(error, BackupTooNewError):
        return ErrorMessage(
            title,
            QCoreApplication.translate(
                "Backup", "This backup was made by a newer version of Ponos Coach."
            ),
            QCoreApplication.translate("Backup", "Update Ponos Coach and try again."),
        )
    if isinstance(error, BackupInvalidError):
        return ErrorMessage(
            title,
            _invalid_reason(error.problem),
            QCoreApplication.translate("Backup", "Choose another backup."),
        )
    if isinstance(error, FolderNotEmptyError):
        return ErrorMessage(
            title,
            QCoreApplication.translate("Backup", "The chosen folder is not empty."),
            QCoreApplication.translate(
                "Backup", "Choose a new folder or an empty one. Nothing was changed."
            ),
        )
    if isinstance(error, ProfileInUseError):
        return ErrorMessage(
            title,
            QCoreApplication.translate(
                "Backup", "The chosen folder is in use by another instance of Ponos Coach."
            ),
            QCoreApplication.translate("Backup", "Choose another folder."),
        )
    if isinstance(error, FolderUnavailableError | BackupWriteError | ValueError):
        return ErrorMessage(
            title,
            QCoreApplication.translate(
                "Backup", "The backup could not be read or the folder could not be written."
            ),
            QCoreApplication.translate(
                "Backup",
                "Check that the file is readable and the folder is writable, with free space.",
            ),
        )
    return ErrorMessage(
        title,
        QCoreApplication.translate("Backup", "Something unexpected happened."),
        QCoreApplication.translate(
            "Backup", "Try again; if it happens again, look at the log (Help menu)."
        ),
    )


def _invalid_reason(problem: BackupProblem) -> str:
    match problem:
        case BackupProblem.NOT_A_BACKUP:
            return QCoreApplication.translate("Backup", "This file is not a Ponos Coach backup.")
        case BackupProblem.LIMIT:
            return QCoreApplication.translate(
                "Backup", "This backup is larger than the limits Ponos Coach accepts."
            )
        case BackupProblem.CHECKSUM:
            return QCoreApplication.translate(
                "Backup", "This backup is damaged: a file does not match its checksum."
            )
        case BackupProblem.UNSAFE_ENTRY:
            return QCoreApplication.translate(
                "Backup", "This backup holds files that a backup should never hold."
            )
        case BackupProblem.DATABASE_INVALID:
            return QCoreApplication.translate(
                "Backup", "This backup is damaged: its database is not intact."
            )
