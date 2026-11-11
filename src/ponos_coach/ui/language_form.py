# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Language page: English or Italian.

The choice is remembered at once and applies the next time the app starts: translating every
window, menu and chart while they are open would duplicate every text, for a change made once.
The page says so when the chosen language is not the one running.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QLabel, QRadioButton, QVBoxLayout, QWidget

from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_2, SPACE_4
from ponos_coach.ui.translations import Language

_LANGUAGES = (Language.ENGLISH, Language.ITALIAN)


class LanguageForm(SettingsForm):
    """One radio button per interface language."""

    language_chosen = Signal(object)  # Language

    def __init__(self, running: Language, chosen: Language, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._running = running
        self._buttons: dict[Language, QRadioButton] = {}
        group = QButtonGroup(self)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_2)
        # A language's name in the language itself is not translated: it serves to recognise it.
        for language in _LANGUAGES:
            button = QRadioButton(language.native_name, self)
            button.setObjectName(f"language-{language.value}")
            button.setChecked(language is chosen)
            button.toggled.connect(lambda on, lang=language: self._on_toggled(lang, on))
            group.addButton(button)
            column.addWidget(button)
            self._buttons[language] = button
        self._note = QLabel(self.tr("The language changes the next time Ponos Coach starts."), self)
        self._note.setObjectName("languageNote")
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.setWordWrap(True)
        column.addSpacing(SPACE_4)
        column.addWidget(self._note)
        self._show_note(chosen)

    def button(self, language: Language) -> QRadioButton:
        """The radio button of a language."""
        return self._buttons[language]

    @property
    def note_is_shown(self) -> bool:
        """True while the chosen language is not the one running."""
        return not self._note.isHidden()

    def _on_toggled(self, language: Language, on: bool) -> None:
        if on:
            self._show_note(language)
            self.language_chosen.emit(language)

    def _show_note(self, chosen: Language) -> None:
        self._note.setVisible(chosen is not self._running)
