# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form rows: the label points to the field, not to the container that wraps it.

With `QFormLayout.addRow(text, container)` Qt links the label to the container (field plus
messages), and the field stays nameless for screen readers. Here the label is created by hand,
has the field as "buddy" and gives it its text as accessible name, if it does not already have one.
"""

from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QWidget

_THRESHOLD_MAX_CHARS = 3  # 40 to 250
_PACE_MAX_CHARS = 16  # the same limit the domain checks before reading the text


def optional_threshold_edit(parent: QWidget, name: str) -> QLineEdit:
    """Optional threshold: whole bpm, or empty for the default (shown as the placeholder)."""
    edit = QLineEdit(parent)
    edit.setObjectName(name)
    edit.setMaxLength(_THRESHOLD_MAX_CHARS)
    edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,3}"), edit))
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    return edit


def threshold_pace_edit(parent: QWidget, name: str, accessible_name: str, hint: str) -> QLineEdit:
    """Threshold pace field (`m:ss`); `hint` is the placeholder."""
    edit = QLineEdit(parent)
    edit.setObjectName(name)
    edit.setAccessibleName(accessible_name)
    edit.setPlaceholderText(hint)
    edit.setMaxLength(_PACE_MAX_CHARS)
    return edit


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
