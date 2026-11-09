# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Content of the Settings > Sport weights page: one value per sport, humble view on
`SportWeightsForm`.

The page edits a copy: nothing changes until "Save weights". Saving starts with `save_requested`;
the coordinator builds the page again when the use case has finished.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import Sport
from ponos_coach.load.domain import SportWeight
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.settings_form import SettingsForm
from ponos_coach.ui.sport_weights_form import WEIGHT_DECIMALS, WEIGHT_STEP, SportWeightsForm
from ponos_coach.ui.tokens import SPACE_2, SPACE_4


class SportWeightsDialog(SettingsForm):
    """A weight from 0 to 5 for each sport, with "Restore defaults"."""

    save_requested = Signal(object)  # dict[Sport, SportWeight]

    def __init__(self, form: SportWeightsForm, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._saving = False
        self._form = form
        self._boxes: dict[Sport, QDoubleSpinBox] = {}
        self._build()
        self._show_values()
        self._update_buttons()

    def _build(self) -> None:
        explanation = QLabel(
            self.tr(
                "The weight of a sport multiplies the load of each of its activities: 1.00 "
                "leaves the load as calculated, 0 removes the sport from the load. Changing a "
                "weight recalculates every activity."
            ),
            self,
        )
        explanation.setWordWrap(True)
        layout = QFormLayout()
        layout.setVerticalSpacing(SPACE_2)
        for sport in Sport:
            box = QDoubleSpinBox(self)
            box.setObjectName(f"weight-{sport.value}")
            box.setAccessibleName(sport_text(sport))
            box.setRange(0.0, SportWeight.MAX)
            box.setDecimals(WEIGHT_DECIMALS)
            box.setSingleStep(WEIGHT_STEP)
            box.valueChanged.connect(lambda value, s=sport: self._on_value_changed(s, value))
            self._boxes[sport] = box
            layout.addRow(sport_text(sport), box)
        self._buttons = QDialogButtonBox(self)
        self._restore = QPushButton(self.tr("Restore defaults"), self)
        self._buttons.addButton(self._restore, QDialogButtonBox.ButtonRole.ResetRole)
        self._save = self._buttons.addButton(
            self.tr("Save weights"), QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._save.setDefault(True)
        self._restore.clicked.connect(self._on_restore)
        self._buttons.accepted.connect(self._on_save)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(explanation)
        outer.addLayout(layout)
        outer.addWidget(self._buttons)

    # --- Test accessors ---------------------------------------------------------

    @property
    def form(self) -> SportWeightsForm:
        """The form model."""
        return self._form

    def box(self, sport: Sport) -> QDoubleSpinBox:
        """The weight control of a sport."""
        return self._boxes[sport]

    @property
    def restore_button(self) -> QPushButton:
        """Button "Restore defaults"."""
        return self._restore

    @property
    def save_button(self) -> QWidget:
        """Button "Save weights"."""
        return self._save

    # --- Model and view --------------------------------------------------------------

    def _show_values(self) -> None:
        for sport, box in self._boxes.items():
            box.blockSignals(True)
            box.setValue(self._form.value(sport))
            box.blockSignals(False)

    def _update_buttons(self) -> None:
        """ "Save weights" is disabled without changes; "Restore defaults" if they already are the
        defaults."""
        changed = self._form.has_changes
        self._save.setEnabled(changed)
        self._save.setToolTip("" if changed else self.tr("Change at least one weight first."))
        is_default = self._form.is_default
        self._restore.setEnabled(not is_default)
        self._restore.setToolTip(
            self.tr("All weights already have their default value.") if is_default else ""
        )
        self.edited.emit()

    def has_unsaved_changes(self) -> bool:
        """True while a weight differs from the saved one."""
        return self._form.has_changes

    def request_save(self) -> None:
        """Save the weights (the "Save weights" button)."""
        self._on_save()

    def _on_value_changed(self, sport: Sport, value: float) -> None:
        self._form.set_value(sport, value)
        self._update_buttons()

    def _on_restore(self) -> None:
        self._form.restore_defaults()
        self._show_values()
        self._update_buttons()

    def _on_save(self) -> None:
        self.set_busy(True)
        self.save_requested.emit(self._form.build())

    def set_busy(self, busy: bool) -> None:
        """While saving is in progress the page accepts no other edits. Unlocking a page that was
        saving means the save did not complete (`save_failed`); unlocking one that was not (a
        failed read) says nothing about a save, so a close the Settings window is waiting for
        is not cancelled."""
        was_saving = self._saving
        self._saving = busy
        self._save.setEnabled(not busy and self._form.has_changes)
        self._restore.setEnabled(not busy and not self._form.is_default)
        for box in self._boxes.values():
            box.setEnabled(not busy)
        if was_saving and not busy:
            self.save_failed.emit()
