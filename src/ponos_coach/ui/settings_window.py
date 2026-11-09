# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Settings window: the categories on the left, the page of the chosen one on the right.

It is one window for the whole app (the main window owns a single instance): opening it again
brings it forward. It is not modal. Every page keeps its own way of saving; the window only
shows which pages hold unsaved edits (an asterisk on the category) and, when it is closed with
edits still unsaved, asks what to do with them, with a button for each verb.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.settings_page import SettingsCategory, SettingsPage
from ponos_coach.ui.tokens import SIDEBAR_WIDTH, SPACE_2, SPACE_4

_DEFAULT_SIZE = (900, 640)
_MIN_SIZE = (720, 480)
_UNSAVED_MARK = " *"


class SettingsWindow(QWidget):
    """Categories and pages of the settings."""

    def __init__(
        self,
        pages: Sequence[SettingsPage],
        remember_box: Callable[[QMessageBox], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._pages = {page.category: page for page in pages}
        self._order = tuple(page.category for page in pages)
        self._remember_box = remember_box
        self._after_save: Callable[[], None] | None = None
        self._leave_confirmed = False
        self.setObjectName("settingsWindow")
        self.setWindowTitle(self.tr("Settings"))
        self.resize(*_DEFAULT_SIZE)
        self.setMinimumSize(*_MIN_SIZE)
        self._list = QListWidget(self)
        self._list.setObjectName("settingsCategories")
        self._list.setAccessibleName(self.tr("Settings categories"))
        self._list.setFixedWidth(SIDEBAR_WIDTH)
        self._stack = QStackedWidget(self)
        for page in pages:
            QListWidgetItem(page.title, self._list)
            self._stack.addWidget(page)
            page.dirty_changed.connect(self._on_dirty_changed)
            page.save_failed.connect(self._cancel_pending_close)
        self._list.currentRowChanged.connect(self._on_row_changed)
        self._close = QPushButton(self.tr("Close"), self)
        self._close.setObjectName("settingsClose")
        self._close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(SPACE_4, 0, SPACE_4, 0)
        buttons.addStretch(1)
        buttons.addWidget(self._close)
        body = QHBoxLayout()
        body.setSpacing(0)
        body.addWidget(self._list)
        body.addWidget(self._stack, stretch=1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, SPACE_2)
        outer.setSpacing(SPACE_2)
        outer.addLayout(body, stretch=1)
        outer.addLayout(buttons)
        self._list.setCurrentRow(0)

    # --- What the window shows ------

    @property
    def categories(self) -> tuple[SettingsCategory, ...]:
        """The categories, in the order they are listed."""
        return self._order

    @property
    def current_category(self) -> SettingsCategory:
        """The category whose page is in view."""
        return self._order[self._list.currentRow()]

    def page(self, category: SettingsCategory) -> SettingsPage:
        """The page of a category."""
        return self._pages[category]

    def category_label(self, category: SettingsCategory) -> str:
        """What the list says for the category (with the mark of unsaved edits)."""
        return self._list.item(self._order.index(category)).text()

    @property
    def category_list(self) -> QListWidget:
        """The list of categories."""
        return self._list

    @property
    def close_button(self) -> QPushButton:
        """The "Close" button."""
        return self._close

    def show_page(self, category: SettingsCategory) -> None:
        """Open the window (or bring it forward) on the page of `category`."""
        row = self._order.index(category)
        changed = self._list.currentRow() != row
        self._list.setCurrentRow(row)
        was_visible = self.isVisible()
        self.show()
        self.raise_()
        self.activateWindow()
        if was_visible and not changed:
            self._pages[category].notify_shown()  # neither a row change nor a show event says it

    def _on_row_changed(self, row: int) -> None:
        if row < 0:
            return
        page = self._pages[self._order[row]]
        self._stack.setCurrentWidget(page)
        if self.isVisible():
            page.notify_shown()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt
        """Opening the window reads again the content of the page in view."""
        super().showEvent(event)
        self._pages[self.current_category].notify_shown()

    # --- Unsaved edits ------

    def has_unsaved_changes(self) -> bool:
        """True if any page holds edits that are not saved."""
        return any(page.is_dirty for page in self._pages.values())

    def _dirty_pages(self) -> list[SettingsPage]:
        return [page for page in self._pages.values() if page.is_dirty]

    def _on_dirty_changed(self) -> None:
        for row, category in enumerate(self._order):
            page = self._pages[category]
            item = self._list.item(row)
            item.setText(page.title + (_UNSAVED_MARK if page.is_dirty else ""))
            item.setToolTip(self.tr("This page has unsaved changes.") if page.is_dirty else "")
        after_save = self._after_save
        if after_save is not None and not self.has_unsaved_changes():
            self._after_save = None
            after_save()

    def confirm_leaving(self, proceed: Callable[[], None]) -> None:
        """Run `proceed` when no edit would be lost: at once if everything is saved, otherwise
        after the user chose to save (and the saves completed) or to discard the edits. "Keep
        editing" leaves things as they are."""
        dirty = self._dirty_pages()
        if not dirty:
            proceed()
            return
        self._after_save = None
        box = QMessageBox(self)
        box.setObjectName("settingsUnsaved")
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Unsaved changes"))
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(self.tr("Some settings have changes that are not saved."))
        # {pages}: the names of the pages with unsaved changes, separated by commas.
        box.setInformativeText(
            self.tr("Unsaved: {pages}.").format(pages=", ".join(page.title for page in dirty))
        )
        save = box.addButton(self.tr("Save"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(self.tr("Discard"), QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton(self.tr("Keep editing"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.buttonClicked.connect(
            lambda clicked: self._on_unsaved_answer(clicked, save, discard, proceed)
        )
        self._remember_box(box)
        box.open()

    def _on_unsaved_answer(
        self, clicked: object, save: object, discard: object, proceed: Callable[[], None]
    ) -> None:
        if clicked is save:
            self._after_save = proceed
            for page in self._dirty_pages():
                page.save()
        elif clicked is discard:
            for page in self._dirty_pages():
                page.discard()
            proceed()

    def _cancel_pending_close(self) -> None:
        """A save that was meant to end with leaving the window failed: stay."""
        self._after_save = None

    # --- Closing ------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - Qt
        """Closing with unsaved edits asks first; otherwise the window only hides (the pages keep
        what they show)."""
        if self._leave_confirmed or not self.has_unsaved_changes():
            self._leave_confirmed = False
            super().closeEvent(event)
            return
        event.ignore()
        self.confirm_leaving(self._close_without_asking)

    def _close_without_asking(self) -> None:
        self._leave_confirmed = True
        self.close()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt
        """Esc closes the window, like "Close"."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)
