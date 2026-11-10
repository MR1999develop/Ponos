# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""One page of the Settings window: a heading, the content of a category and, when the content
cannot be shown, the reason why.

The page does not know what its content is: whoever owns the data (the coordinator of the profile,
of the plan, of the map...) builds the content and hands it over with `show_form`. The page only
hosts it, says whether it holds unsaved edits, whether it can be saved now and why not, and can
be asked to save.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_4, SPACE_6, TITLE_SCALE


class SettingsCategory(Enum):
    """The categories of the Settings window, in the order they are listed."""

    PROFILE = "profile"
    GARMIN = "garmin"
    TRAINING_PLAN = "training-plan"
    NUTRITION = "nutrition"
    MAP = "map"
    LANGUAGE = "language"


class SettingsPage(QScrollArea):
    """Hosts the content of one category."""

    dirty_changed = Signal()  # `is_dirty` changed
    save_state_changed = Signal()  # `can_save` or `save_blocked_reason` may have changed
    save_failed = Signal()  # a save the content started did not complete: the edits are still here
    shown = Signal()  # the page became the one in view: its content may need to be read again

    def __init__(
        self,
        category: SettingsCategory,
        title: str,
        parent: QWidget | None = None,
        *,
        footer: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._category = category
        self._title = title
        self._form: SettingsForm | None = None
        self._dirty = False
        self.setObjectName(f"settingsPage-{category.value}")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAccessibleName(title)
        content = QWidget(self)
        column = QVBoxLayout(content)
        column.setContentsMargins(SPACE_6, SPACE_6, SPACE_6, SPACE_6)
        column.setSpacing(SPACE_4)
        self._heading = QLabel(title, content)
        self._heading.setObjectName("settingsPageHeading")
        self._heading.setTextFormat(Qt.TextFormat.PlainText)
        heading_font = self._heading.font()
        heading_font.setPointSizeF(heading_font.pointSizeF() * TITLE_SCALE)
        heading_font.setBold(True)
        self._heading.setFont(heading_font)
        column.addWidget(self._heading)
        self._notice = QLabel(content)
        self._notice.setObjectName("settingsPageNotice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setWordWrap(True)
        column.addWidget(self._notice)
        self._slot = QVBoxLayout()
        self._slot.setContentsMargins(0, 0, 0, 0)
        column.addLayout(self._slot)
        if footer is not None:
            column.addWidget(footer)
        column.addStretch(1)
        self.setWidget(content)
        self.show_notice("")

    # --- What the page shows ------

    @property
    def category(self) -> SettingsCategory:
        """The category this page belongs to."""
        return self._category

    @property
    def title(self) -> str:
        """The name of the category, as listed and as the page's heading."""
        return self._title

    @property
    def form(self) -> SettingsForm | None:
        """The content shown, if there is any."""
        return self._form

    @property
    def notice_text(self) -> str:
        """Why there is no content (empty while the content is shown)."""
        return "" if self._notice.isHidden() else self._notice.text()

    def show_form(self, form: SettingsForm) -> None:
        """Show `form` in place of what the page showed (the previous content is dropped)."""
        self._drop_form()
        self._form = form
        self._slot.addWidget(form)
        form.edited.connect(self._on_form_edited)
        form.save_failed.connect(self.save_failed)
        self._notice.hide()
        self._on_form_edited()

    def show_notice(self, text: str) -> None:
        """Drop the content and say why there is none (`text` empty: nothing to say yet)."""
        self._drop_form()
        self._notice.setText(text)
        self._notice.setVisible(bool(text))
        self._on_form_edited()

    def _drop_form(self) -> None:
        form, self._form = self._form, None
        if form is not None:
            form.edited.disconnect(self._on_form_edited)
            form.save_failed.disconnect(self.save_failed)
            self._slot.removeWidget(form)
            form.setParent(None)
            form.deleteLater()

    # --- Unsaved edits ------

    @property
    def is_dirty(self) -> bool:
        """True while the content holds edits that are not saved."""
        return self._dirty

    @property
    def can_save(self) -> bool:
        """True if the content can be saved now (the window enables its button on it)."""
        return self._form is not None and self._form.can_save()

    @property
    def save_blocked_reason(self) -> str:
        """Why the content cannot be saved now (empty: no particular reason to tell)."""
        return "" if self._form is None else self._form.save_blocked_reason()

    def save(self) -> None:
        """Ask the content to save what is typed."""
        if self._form is not None:
            self._form.request_save()

    def discard(self) -> None:
        """Drop what was typed: the content is built again from what is saved."""
        self.show_notice("")
        self.shown.emit()

    def _on_form_edited(self) -> None:
        self._refresh_dirty()
        self.save_state_changed.emit()

    def _refresh_dirty(self) -> None:
        dirty = self._form is not None and self._form.has_unsaved_changes()
        if dirty != self._dirty:
            self._dirty = dirty
            self.dirty_changed.emit()

    def notify_shown(self) -> None:
        """Tell the owner of the content that the page is in view."""
        self.shown.emit()
