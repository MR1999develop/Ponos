# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Interface language: chooses Italian or English and installs Qt's translators.

The default language is English (not the system's); the user can choose another from the
Language menu and the choice stays in the settings, applied at the next launch; `--lang` takes
precedence. The source texts in the code are the English version: for English nothing
is installed.
Italian needs `ponos_it.qm` (generated from `ponos_it.ts` with `nox -s i18n`, ignored by
git) and, for Qt's standard dialogs, `qtbase_it.qm` from PySide6's translations folder.
A missing `.qm` is not an error for the user: the app starts in English and writes it to the log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QSettings, QTranslator

_LOG = logging.getLogger(__name__)

LANGUAGE_KEY = "language/code"
APP_CATALOG = "ponos_it.qm"
QT_CATALOG = "qtbase_it.qm"


class Language(StrEnum):
    """Interface languages; the value is the ISO 639-1 code."""

    ITALIAN = "it"
    ENGLISH = "en"

    @property
    def native_name(self) -> str:
        """The language's name in the language itself: not translated (it serves to recognise
        it)."""
        return "Italiano" if self is Language.ITALIAN else "English"


@dataclass(frozen=True, slots=True)
class TranslationReport:
    """Installation outcome; `translators` serves whoever must remove them (tests)."""

    language: Language
    app_loaded: bool
    qt_loaded: bool
    translators: tuple[QTranslator, ...]


def choose_language(forced: Language | None, saved: Language | None) -> Language:
    """The forced language (`--lang`), otherwise the one chosen by the user, otherwise English."""
    if forced is not None:
        return forced
    return Language.ENGLISH if saved is None else saved


def saved_language(settings: QSettings) -> Language | None:
    """The language chosen by the user; a missing, damaged or unknown value means "none"."""
    raw = settings.value(LANGUAGE_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return Language(raw)
    except ValueError:
        return None


def save_language(settings: QSettings, language: Language) -> None:
    """Remember the chosen language for the next launches."""
    settings.setValue(LANGUAGE_KEY, language.value)
    settings.sync()


def app_translations_dir() -> Path:
    """Folder of the app's catalogues (`src/ponos_coach/i18n`)."""
    return Path(__file__).resolve().parents[1] / "i18n"


def qt_translations_dir() -> Path:
    """Folder of the Qt translations shipped with PySide6."""
    return Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))


def install_translators(
    app: QCoreApplication, language: Language, *, app_dir: Path, qt_dir: Path
) -> TranslationReport:
    """Install the translators for `language`; missing ones are written once to the log."""
    if language is Language.ENGLISH:
        return TranslationReport(language, app_loaded=False, qt_loaded=False, translators=())
    installed: list[QTranslator] = []
    loaded: dict[str, bool] = {}
    for name, directory in ((APP_CATALOG, app_dir), (QT_CATALOG, qt_dir)):
        translator = QTranslator(app)  # child of the app: lives as long as it does
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
