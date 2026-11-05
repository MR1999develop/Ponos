# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Messaggi d'errore per l'utente: l'unico punto in cui un'eccezione diventa testo."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QCoreApplication

from ponos_coach.activities.ports import FitFileUnreadableError, FitSourceError
from ponos_coach.load.use_cases import RejectionReason

_CONTEXT = "Errors"


def _tr(text: str) -> str:
    return QCoreApplication.translate(_CONTEXT, text)


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
            _tr("The folder could not be read"),
            _tr("Ponos Coach could not list the FIT files in the chosen folder."),
            _tr("Check that the folder exists and that you can open it, then choose it again."),
        )
    if isinstance(error, FitSourceError):
        return ErrorMessage(
            _tr("The FIT files could not be read"),
            _tr("The source of the FIT files stopped working."),
            _tr("Choose the folder again. If the problem persists, check the application log."),
        )
    return ErrorMessage(
        _tr("Something went wrong"),
        _tr("The fitness overview could not be calculated."),
        _tr("Try again. If the problem persists, check the application log."),
    )


def rejection_reason_text(reason: RejectionReason) -> str:
    """Perche un file e stato escluso, in una frase breve."""
    texts = {
        RejectionReason.EMPTY: _tr("empty file"),
        RejectionReason.TOO_LARGE: _tr("file too large"),
        RejectionReason.CORRUPT: _tr("damaged or not a FIT file"),
        RejectionReason.NOT_AN_ACTIVITY: _tr("not an activity"),
        RejectionReason.TOO_MANY_RECORDS: _tr("too many records"),
        RejectionReason.TOO_LONG: _tr("activity too long"),
        RejectionReason.UNREADABLE: _tr("could not be read"),
        RejectionReason.DUPLICATE: _tr("duplicate of another file"),
    }
    return texts[reason]
