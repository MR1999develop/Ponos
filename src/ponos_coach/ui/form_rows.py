# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form rows: the label points to the field, not to the container that wraps it.

With `QFormLayout.addRow(text, container)` Qt links the label to the container (field plus
messages), and the field stays nameless for screen readers. Here the label is created by hand,
has the field as "buddy" and gives it its text as accessible name, if it does not already have one.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QFormLayout, QLabel, QLineEdit, QWidget

from ponos_coach.ui.tokens import SPACE_6

_THRESHOLD_MAX_CHARS = 3  # 40 to 250
_PACE_MAX_CHARS = 16  # the same limit the domain checks before reading the text


class FieldKind(Enum):
    """The kinds of content that give a form field its width."""

    SHORT = "short"  # a number with its unit (bpm, kg, cm, h), m:ss, a time of day
    DATE = "date"  # a short date, with the room of the calendar button
    CHOICE = "choice"  # one of a few short words (a combo box)


# The widest text of each kind, not translated: the width comes from the metrics of the font in
# use, so it holds with the system's font and with longer words (Italian).
_SAMPLES = {
    FieldKind.SHORT: "000.00 bpm",
    FieldKind.DATE: "00/00/0000",
    FieldKind.CHOICE: "Molto attivo",
}
_CONTROL_ROOM = 2 * SPACE_6  # frame plus spin buttons, calendar button or arrow


def field_width(widget: QWidget, kind: FieldKind) -> int:
    """Width in pixels of a field of `kind`, measured with the font of `widget`."""
    return widget.fontMetrics().horizontalAdvance(_SAMPLES[kind]) + _CONTROL_ROOM


def size_field(widget: QWidget, kind: FieldKind) -> None:
    """Give `widget` the width of its kind: every field of a kind is as wide as the others, in
    every page, and none stretches to the edge of its column."""
    widget.setFixedWidth(field_width(widget, kind))


def optional_threshold_edit(parent: QWidget, name: str) -> QLineEdit:
    """Optional threshold: whole bpm, or empty for the default (shown as the placeholder)."""
    edit = QLineEdit(parent)
    edit.setObjectName(name)
    edit.setMaxLength(_THRESHOLD_MAX_CHARS)
    edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[0-9]{0,3}"), edit))
    edit.setAlignment(Qt.AlignmentFlag.AlignRight)
    size_field(edit, FieldKind.SHORT)
    return edit


def threshold_pace_edit(parent: QWidget, name: str, accessible_name: str, hint: str) -> QLineEdit:
    """Threshold pace field (`m:ss`); `hint` is the placeholder."""
    edit = QLineEdit(parent)
    edit.setObjectName(name)
    edit.setAccessibleName(accessible_name)
    edit.setPlaceholderText(hint)
    edit.setMaxLength(_PACE_MAX_CHARS)
    size_field(edit, FieldKind.SHORT)
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
