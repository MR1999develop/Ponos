# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Dialogo "Pesi degli sport": un valore per sport, vista umile su `SportWeightsForm`.

Il dialogo modifica una copia: "Annulla" non cambia nulla. Il salvataggio parte con
`save_requested`; chiude la finestra quando il caso d'uso ha finito.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
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
from ponos_coach.ui.sport_weights_form import WEIGHT_DECIMALS, WEIGHT_STEP, SportWeightsForm
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4


class SportWeightsDialog(QDialog):
    """Un peso da 0 a 5 per ogni sport, con "Ripristina predefiniti"."""

    save_requested = Signal(object)  # dict[Sport, SportWeight]

    def __init__(self, form: SportWeightsForm, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._form = form
        self._boxes: dict[Sport, QDoubleSpinBox] = {}
        self.setWindowTitle(self.tr("Sport weights"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
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
        self._buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self._restore.clicked.connect(self._on_restore)
        self._buttons.accepted.connect(self._on_save)
        self._buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(explanation)
        outer.addLayout(layout)
        outer.addWidget(self._buttons)

    # --- Accessori per i test ---------------------------------------------------------

    @property
    def form(self) -> SportWeightsForm:
        """Il modello del modulo."""
        return self._form

    def box(self, sport: Sport) -> QDoubleSpinBox:
        """Il controllo del peso di uno sport."""
        return self._boxes[sport]

    @property
    def restore_button(self) -> QPushButton:
        """Bottone "Ripristina predefiniti"."""
        return self._restore

    @property
    def save_button(self) -> QWidget:
        """Bottone "Salva pesi"."""
        return self._save

    # --- Modello e vista --------------------------------------------------------------

    def _show_values(self) -> None:
        for sport, box in self._boxes.items():
            box.blockSignals(True)
            box.setValue(self._form.value(sport))
            box.blockSignals(False)

    def _update_buttons(self) -> None:
        """ "Salva pesi" è disabilitato senza modifiche; "Ripristina" se sono già i predefiniti."""
        changed = self._form.has_changes
        self._save.setEnabled(changed)
        self._save.setToolTip("" if changed else self.tr("Change at least one weight first."))
        is_default = self._form.is_default
        self._restore.setEnabled(not is_default)
        self._restore.setToolTip(
            self.tr("All weights already have their default value.") if is_default else ""
        )

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
        """Mentre il salvataggio è in corso il dialogo non accetta altre modifiche."""
        self._save.setEnabled(not busy and self._form.has_changes)
        self._restore.setEnabled(not busy and not self._form.is_default)
        for box in self._boxes.values():
            box.setEnabled(not busy)
