# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Map page: the size of the tile cache and "Clear map cache".

A humble view of `MapSettingsModel`. The cache size applies when the field is left (no Apply
button); clearing the cache asks first, says how much it frees and names the action on its button.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.maps.domain import GIB, MAX_CACHE_BYTES, MIN_CACHE_BYTES
from ponos_coach.ui.map_settings import MapSettingsModel
from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_2, SPACE_4


class MapSettingsDialog(SettingsForm):
    """Settings of the map: cache size and clearing it."""

    cache_cleared = Signal()

    def __init__(self, model: MapSettingsModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mapSettingsDialog")
        self._model = model
        self._boxes: list[QMessageBox] = []
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(self._build_cache_group())
        layout.addStretch(1)
        self.refresh()

    # --- Parts -------------------------------------------------------------------------

    def _build_cache_group(self) -> QWidget:
        group = QGroupBox(self.tr("Map cache"), self)
        form = QFormLayout(group)
        form.setSpacing(SPACE_2)
        self._cache_field = QLineEdit(group)
        self._cache_field.setObjectName("cacheSize")
        self._cache_field.setAccessibleName(self.tr("Cache size (GB)"))
        self._cache_field.editingFinished.connect(self.apply_cache_size)
        form.addRow(self.tr("Cache size (GB)"), self._cache_field)
        self._cache_message = QLabel(group)
        self._cache_message.setObjectName("cacheMessage")
        self._cache_message.setWordWrap(True)
        self._cache_message.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self._cache_message)
        self._clear_button = QPushButton(self.tr("Clear map cache..."), group)
        self._clear_button.setObjectName("clearCache")
        self._clear_button.clicked.connect(self.ask_clear_cache)
        form.addRow(self._clear_button)
        return group

    # --- Access for tests ------------------------------------------------------------------

    @property
    def cache_field(self) -> QLineEdit:
        """The field with the cache size in GiB."""
        return self._cache_field

    @property
    def clear_button(self) -> QPushButton:
        """The "Clear map cache..." button."""
        return self._clear_button

    @property
    def cache_message_text(self) -> str:
        """The cache's size message (used, or what is wrong with the typed size)."""
        return self._cache_message.text()

    @property
    def open_boxes(self) -> list[QMessageBox]:
        """The confirmation boxes still open."""
        return [box for box in self._boxes if box.isVisible()]

    # --- Showing -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the current state of the cache."""
        limit = self._model.cache_limit()
        self._cache_field.setText(QLocale().toString(limit.bytes / GIB, "g"))
        self._show_cache_usage()

    def _show_cache_usage(self) -> None:
        used = QLocale().formattedDataSize(self._model.cache_used_bytes())
        total = QLocale().formattedDataSize(self._model.cache_limit().bytes)
        # {used}: space taken by the saved tiles; {total}: the chosen cap.
        self._cache_message.setText(
            self.tr("{used} used of {total}. Only tiles you looked at are saved.").format(
                used=used, total=total
            )
        )

    # --- Cache -------------------------------------------------------------------------------

    def apply_cache_size(self) -> None:
        """Apply the size typed (from 64 MB to 64 GB); a wrong one is explained and not applied."""
        result = self._model.set_cache_limit(self._cache_field.text())
        if result.limit is None:
            low = QLocale().formattedDataSize(MIN_CACHE_BYTES)
            high = QLocale().formattedDataSize(MAX_CACHE_BYTES)
            # {low}, {high}: the smallest and the largest size allowed.
            self._cache_message.setText(
                self.tr("Enter a size in GB between {low} and {high}, for example 4.").format(
                    low=low, high=high
                )
            )
            return
        self.refresh()

    def ask_clear_cache(self) -> None:
        """Ask before emptying the cache, saying how much it frees."""
        freed = QLocale().formattedDataSize(self._model.cache_used_bytes())
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(self.tr("Clear map cache"))
        # {size}: the space that will be freed.
        box.setText(self.tr("Clear the map cache? This frees {size}.").format(size=freed))
        box.setInformativeText(
            self.tr("The map downloads the tiles again when you look at those areas.")
        )
        clear = box.addButton(self.tr("Clear cache"), QMessageBox.ButtonRole.DestructiveRole)
        keep = box.addButton(self.tr("Keep cache"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.buttonClicked.connect(lambda clicked: self._on_clear_answer(clicked is clear))
        self._boxes.append(box)
        box.open()

    def _on_clear_answer(self, confirmed: bool) -> None:
        if not confirmed:
            return
        if self._model.clear_cache() is None:
            self._cache_message.setText(
                self.tr("The cache could not be emptied. Close the app and delete it by hand.")
            )
            return
        self.cache_cleared.emit()
        self.refresh()
