# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Map page: the Tracestrack key, the size of the tile cache, "Clear map
cache".

A humble view of `MapSettingsModel`. The key field hides what is typed and the page never shows
a saved key, only whether there is one. The cache size applies when the field is left (no Apply
button); clearing the cache asks first, says how much it frees and names the action on its button.
"""

from __future__ import annotations

from PySide6.QtCore import QLocale, Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.maps.domain import GIB, MAX_CACHE_BYTES, MIN_CACHE_BYTES
from ponos_coach.ui.map_settings import KeyOutcome, MapSettingsModel, outcome_text
from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.tokens import SPACE_2, SPACE_4


class MapSettingsDialog(SettingsForm):
    """Settings of the map. `key_changed` tells the window that the key was saved or removed."""

    key_changed = Signal()
    cache_cleared = Signal()

    def __init__(self, model: MapSettingsModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mapSettingsDialog")
        self._model = model
        self._boxes: list[QMessageBox] = []
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_4)
        layout.addWidget(self._build_key_group())
        layout.addWidget(self._build_cache_group())
        layout.addStretch(1)
        model.key_finished.connect(self._on_key_finished)
        self.refresh()

    # --- Parts -------------------------------------------------------------------------

    def _build_key_group(self) -> QWidget:
        group = QGroupBox(self.tr("Tracestrack key"), self)
        column = QVBoxLayout(group)
        column.setSpacing(SPACE_2)
        intro = QLabel(
            self.tr(
                "The map background comes from Tracestrack (Topo style, data from OpenStreetMap). "
                "It needs your own key, which stays in the system key store, outside your profile "
                "folder. Without a key the map has a plain background; tracks, filters and the "
                "list work anyway."
            ),
            group,
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.PlainText)
        column.addWidget(intro)
        self._key_status = QLabel(group)
        self._key_status.setObjectName("keyStatus")
        column.addWidget(self._key_status)
        self._key_field = QLineEdit(group)
        self._key_field.setObjectName("keyField")
        self._key_field.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_field.setAccessibleName(self.tr("Tracestrack key"))
        self._key_field.setPlaceholderText(self.tr("Paste your key here"))
        self._key_field.returnPressed.connect(self.save_key)
        self._key_field.textChanged.connect(self.edited)
        column.addWidget(self._key_field)
        row = QHBoxLayout()
        self._save_button = QPushButton(self.tr("Save key"), group)
        self._save_button.setObjectName("saveKey")
        self._save_button.setDefault(True)
        self._save_button.clicked.connect(self.save_key)
        self._remove_button = QPushButton(self.tr("Remove key"), group)
        self._remove_button.setObjectName("removeKey")
        self._remove_button.clicked.connect(self.remove_key)
        row.addWidget(self._save_button)
        row.addWidget(self._remove_button)
        row.addStretch(1)
        column.addLayout(row)
        self._key_message = QLabel(group)
        self._key_message.setObjectName("keyMessage")
        self._key_message.setWordWrap(True)
        self._key_message.setTextFormat(Qt.TextFormat.PlainText)
        column.addWidget(self._key_message)
        return group

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
    def key_field(self) -> QLineEdit:
        """The field where the key is typed (it hides what is typed)."""
        return self._key_field

    @property
    def cache_field(self) -> QLineEdit:
        """The field with the cache size in GiB."""
        return self._cache_field

    @property
    def save_button(self) -> QPushButton:
        """The "Save key" button."""
        return self._save_button

    @property
    def remove_button(self) -> QPushButton:
        """The "Remove key" button."""
        return self._remove_button

    @property
    def clear_button(self) -> QPushButton:
        """The "Clear map cache..." button."""
        return self._clear_button

    @property
    def key_status_text(self) -> str:
        """Whether a key is saved, in words."""
        return self._key_status.text()

    @property
    def key_message_text(self) -> str:
        """The result of the last key action."""
        return self._key_message.text()

    @property
    def cache_message_text(self) -> str:
        """The cache's size message (used, or what is wrong with the typed size)."""
        return self._cache_message.text()

    @property
    def open_boxes(self) -> list[QMessageBox]:
        """The confirmation boxes still open."""
        return [box for box in self._boxes if box.isVisible()]

    def has_unsaved_changes(self) -> bool:
        """True while a key is typed and not saved (the cache size applies when the field is
        left)."""
        return bool(self._key_field.text())

    def request_save(self) -> None:
        """Check and save the typed key (the "Save key" button)."""
        self.save_key()

    # --- Showing -----------------------------------------------------------------------------

    def refresh(self) -> None:
        """Show the current state of the key and of the cache."""
        has_key = self._model.has_key()
        self._key_status.setText(
            self.tr("A key is saved.") if has_key else self.tr("No key is saved.")
        )
        self._remove_button.setEnabled(has_key and not self._model.is_checking)
        self._remove_button.setToolTip("" if has_key else self.tr("There is no key to remove."))
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

    # --- Key ---------------------------------------------------------------------------------

    def save_key(self) -> None:
        """Check the key typed with Tracestrack and save it unless it is refused."""
        if not self._model.save_key(self._key_field.text()):
            self.save_failed.emit()
            return
        self._save_button.setEnabled(False)
        self._remove_button.setEnabled(False)
        self._key_message.setText(self.tr("Checking the key with Tracestrack..."))

    def remove_key(self) -> None:
        """Forget the saved key."""
        self._report_key(self._model.remove_key())

    def _on_key_finished(self, outcome: object) -> None:
        if isinstance(outcome, KeyOutcome):
            self._report_key(outcome)

    def _report_key(self, outcome: KeyOutcome) -> None:
        self._save_button.setEnabled(True)
        self._key_message.setText(outcome_text(outcome))
        if outcome in {KeyOutcome.ACCEPTED, KeyOutcome.SAVED_UNVERIFIED, KeyOutcome.REMOVED}:
            self._key_field.clear()  # the typed key does not stay on screen
            self.key_changed.emit()
        else:
            self.save_failed.emit()
        self.refresh()

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
            self.tr(
                "The map downloads the tiles again when you look at those areas, which uses your "
                "Tracestrack credit."
            )
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
