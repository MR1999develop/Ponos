# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Lingua dell'interfaccia: sceglie italiano o inglese e installa i traduttori di Qt.

I testi sorgente nel codice sono la versione inglese: per l'inglese non si installa nulla.
Per l'italiano servono `ponos_it.qm` (generato da `ponos_it.ts` con `nox -s i18n`, ignorato da
git) e, per i dialoghi standard di Qt, `qtbase_it.qm` dalla cartella delle traduzioni di PySide6.
Un `.qm` mancante non e un errore per l'utente: l'app parte in inglese e lo scrive nel log.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QTranslator

_LOG = logging.getLogger(__name__)

APP_CATALOG = "ponos_it.qm"
QT_CATALOG = "qtbase_it.qm"


class Language(StrEnum):
    """Lingue dell'interfaccia; il valore e il codice ISO 639-1."""

    ITALIAN = "it"
    ENGLISH = "en"


@dataclass(frozen=True, slots=True)
class TranslationReport:
    """Esito dell'installazione; `translators` serve a chi deve toglierli (test)."""

    language: Language
    app_loaded: bool
    qt_loaded: bool
    translators: tuple[QTranslator, ...]


def choose_language(forced: Language | None, ui_languages: Sequence[str]) -> Language:
    """La lingua forzata, altrimenti quella preferita dal sistema (la prima di `ui_languages`).

    Italiano solo se la lingua preferita e l'italiano (`it`, `it-IT`, `it_CH`...); altrimenti
    inglese, anche per elenchi vuoti o codici sconosciuti.
    """
    if forced is not None:
        return forced
    if not ui_languages:
        return Language.ENGLISH
    primary = ui_languages[0].replace("_", "-").split("-", maxsplit=1)[0].lower()
    return Language.ITALIAN if primary == Language.ITALIAN.value else Language.ENGLISH


def app_translations_dir() -> Path:
    """Cartella dei cataloghi dell'app (`src/ponos_coach/i18n`)."""
    return Path(__file__).resolve().parents[1] / "i18n"


def qt_translations_dir() -> Path:
    """Cartella delle traduzioni di Qt distribuita con PySide6."""
    return Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))


def install_translators(
    app: QCoreApplication, language: Language, *, app_dir: Path, qt_dir: Path
) -> TranslationReport:
    """Installa i traduttori per `language`; i mancanti si scrivono una volta nel log."""
    if language is Language.ENGLISH:
        return TranslationReport(language, app_loaded=False, qt_loaded=False, translators=())
    installed: list[QTranslator] = []
    loaded: dict[str, bool] = {}
    for name, directory in ((APP_CATALOG, app_dir), (QT_CATALOG, qt_dir)):
        translator = QTranslator(app)  # figlio dell'app: vive quanto lei
        path = directory / name
        loaded[name] = path.is_file() and translator.load(str(path))
        if loaded[name]:
            app.installTranslator(translator)
            installed.append(translator)
        else:
            _LOG.warning("Translation catalog %s not available: using English", name)
            translator.deleteLater()
    return TranslationReport(
        language,
        app_loaded=loaded[APP_CATALOG],
        qt_loaded=loaded[QT_CATALOG],
        translators=tuple(installed),
    )
