# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Righe dei moduli: l'etichetta punta al campo, non al contenitore che lo avvolge.

Con `QFormLayout.addRow(testo, contenitore)` Qt collega l'etichetta al contenitore (campo più
messaggi), e il campo resta senza nome per i lettori di schermo. Qui l'etichetta è creata a mano,
ha il campo come "buddy" e gli dà il suo testo come nome accessibile, se non ne ha già uno.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget


def add_field_row(
    layout: QFormLayout, text: str, field: QWidget, container: QWidget | None = None
) -> None:
    """Aggiunge una riga con l'etichetta `text` per `field`; `container` (campo più note e
    messaggi) è ciò che sta nella colonna dei campi, `field` stesso se omesso."""
    label = QLabel(text)
    label.setBuddy(field)
    if not field.accessibleName():
        field.setAccessibleName(text)
    layout.addRow(label, field if container is None else container)
