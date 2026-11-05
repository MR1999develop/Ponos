# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Messaggi d'errore per l'utente: l'unico punto in cui un'eccezione diventa testo."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.ports import FitFileUnreadableError, FitSourceError
from ponos_coach.load.use_cases import RejectionReason

# Contesto "Errors": lupdate trova solo contesto e testo scritti come letterali, quindi
# niente alias di `QCoreApplication.translate`.


@dataclass(frozen=True, slots=True)
class ErrorMessage:
    """Cosa e successo e come rimediare, in linguaggio comune."""

    title: str
    what: str
    remedy: str


def describe_error(error: BaseException) -> ErrorMessage:
    """Messaggio per l'utente di un'eccezione; mai il testo dell'eccezione."""
    if isinstance(error, FitFileUnreadableError):
        return ErrorMessage(
            QCoreApplication.translate("Errors", "The folder could not be read"),
            QCoreApplication.translate(
                "Errors", "Ponos Coach could not list the FIT files in the chosen folder."
            ),
            QCoreApplication.translate(
                "Errors",
                "Check that the folder exists and that you can open it, then choose it again.",
            ),
        )
    if isinstance(error, FitSourceError):
        return ErrorMessage(
            QCoreApplication.translate("Errors", "The FIT files could not be read"),
            QCoreApplication.translate("Errors", "The source of the FIT files stopped working."),
            QCoreApplication.translate(
                "Errors",
                "Choose the folder again. If the problem persists, check the application log.",
            ),
        )
    return ErrorMessage(
        QCoreApplication.translate("Errors", "Something went wrong"),
        QCoreApplication.translate("Errors", "The fitness overview could not be calculated."),
        QCoreApplication.translate(
            "Errors", "Try again. If the problem persists, check the application log."
        ),
    )


def rejection_reason_text(reason: RejectionReason) -> str:
    """Perche un file e stato escluso, in una frase breve."""
    texts = {
        RejectionReason.EMPTY: QCoreApplication.translate("Errors", "empty file"),
        RejectionReason.TOO_LARGE: QCoreApplication.translate("Errors", "file too large"),
        RejectionReason.CORRUPT: QCoreApplication.translate("Errors", "damaged or not a FIT file"),
        RejectionReason.NOT_AN_ACTIVITY: QCoreApplication.translate("Errors", "not an activity"),
        RejectionReason.TOO_MANY_RECORDS: QCoreApplication.translate("Errors", "too many records"),
        RejectionReason.TOO_LONG: QCoreApplication.translate("Errors", "activity too long"),
        RejectionReason.UNREADABLE: QCoreApplication.translate("Errors", "could not be read"),
        RejectionReason.DUPLICATE: QCoreApplication.translate(
            "Errors", "duplicate of another file"
        ),
    }
    return texts[reason]
