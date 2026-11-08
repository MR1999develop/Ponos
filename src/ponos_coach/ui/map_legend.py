# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Legend of the map: a sample line (colour and dash pattern) and a name for every entry.

The sample repeats what the page draws, so the legend is true to the map; the name carries the
meaning, so the colour is never the only sign.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_4

_SAMPLE_WIDTH = 32
_SAMPLE_HEIGHT = 16
_LINE_WIDTH = 3.0


@dataclass(frozen=True, slots=True)
class LegendEntry:
    """A name, the colour of its line and the dash pattern ("" = solid, "10 5" = on/off lengths)."""

    text: str
    color: str
    dash: str = ""


def dash_pattern(dash: str) -> list[float]:
    """The on/off lengths of a dash text in units of the pen width (empty for a solid line)."""
    if not dash:
        return []
    lengths = [float(part) / _LINE_WIDTH for part in dash.split()]
    return lengths if len(lengths) % 2 == 0 else lengths * 2  # Qt wants pairs


class _Sample(QWidget):
    """The little line drawn before a name."""

    def __init__(self, entry: LegendEntry, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setFixedSize(QSize(_SAMPLE_WIDTH, _SAMPLE_HEIGHT))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt
        """Draw the line with the colour and dash of the entry."""
        del event
        painter = QPainter(self)
        pen = QPen(QColor(self._entry.color), _LINE_WIDTH)
        pattern = dash_pattern(self._entry.dash)
        if pattern:
            pen.setDashPattern(pattern)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        middle = self.height() // 2
        painter.drawLine(0, middle, self.width(), middle)
        painter.end()


class LegendBar(QWidget):
    """A row of legend entries; hidden when it has none."""

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("mapLegend")
        self.setAccessibleName(accessible_name)
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(SPACE_4, SPACE_1, SPACE_4, SPACE_1)
        self._row.setSpacing(SPACE_4)
        self._entries: tuple[LegendEntry, ...] = ()
        self.hide()

    @property
    def entries(self) -> tuple[LegendEntry, ...]:
        """The entries shown now."""
        return self._entries

    def set_entries(self, entries: Sequence[LegendEntry]) -> None:
        """Replace the entries (the bar hides itself if there are none)."""
        if tuple(entries) == self._entries:
            return
        self._entries = tuple(entries)
        while (item := self._row.takeAt(0)) is not None:
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for entry in self._entries:
            pair = QWidget(self)
            inner = QHBoxLayout(pair)
            inner.setContentsMargins(0, 0, 0, 0)
            inner.setSpacing(SPACE_2)
            inner.addWidget(_Sample(entry, pair))
            label = QLabel(entry.text, pair)
            label.setTextFormat(Qt.TextFormat.PlainText)
            inner.addWidget(label)
            self._row.addWidget(pair)
        self._row.addStretch(1)
        self.setVisible(bool(self._entries))
