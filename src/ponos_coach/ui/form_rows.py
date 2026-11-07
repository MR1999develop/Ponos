# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form rows: the label points to the field, not to the container that wraps it.

With `QFormLayout.addRow(text, container)` Qt links the label to the container (field plus
messages), and the field stays nameless for screen readers. Here the label is created by hand,
has the field as "buddy" and gives it its text as accessible name, if it does not already have one.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFormLayout, QLabel, QWidget


def add_field_row(
    layout: QFormLayout, text: str, field: QWidget, container: QWidget | None = None
) -> None:
    """Add a row with the label `text` for `field`; `container` (field plus notes and
    messages) is what goes in the field column, `field` itself if omitted."""
    label = QLabel(text)
    label.setBuddy(field)
    if not field.accessibleName():
        field.setAccessibleName(text)
    layout.addRow(label, field if container is None else container)
