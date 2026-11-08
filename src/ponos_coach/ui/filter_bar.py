# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The filter bar above the activity table and the map: dates, duration, distance, ascent, sports.

A humble view of `SharedFilterState`: the fields hold text, `editingFinished` (Enter or leaving the
field) hands the texts to the state, which decides; a wrong field is marked and the bar says, in
words, what to write instead. An empty field is no limit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import QCoreApplication, QDate, QLocale, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import Sport
from ponos_coach.ui.activity_filters import (
    FieldProblem,
    FilterField,
    FilterTexts,
    SharedFilterState,
    field_label,
    problem_text,
)
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_4, Theme, tokens_for

_FIELD_MIN_WIDTH = 96
_NAME_OF = {field: field.value for field in FilterField}
_GROUPS: tuple[tuple[str, FilterField, FilterField], ...] = (
    ("date", FilterField.DATE_FROM, FilterField.DATE_TO),
    ("duration", FilterField.DURATION_MIN, FilterField.DURATION_MAX),
    ("distance", FilterField.DISTANCE_MIN, FilterField.DISTANCE_MAX),
    ("ascent", FilterField.ASCENT_MIN, FilterField.ASCENT_MAX),
)


def _group_title(kind: str) -> str:
    titles = {
        "date": QCoreApplication.translate("Filters", "Date"),
        "duration": QCoreApplication.translate("Filters", "Duration (min)"),
        "distance": QCoreApplication.translate("Filters", "Distance (km)"),
        "ascent": QCoreApplication.translate("Filters", "Ascent (m)"),
    }
    return titles[kind]


class FilterBar(QWidget):
    """Filter fields bound to a `SharedFilterState`."""

    def __init__(
        self,
        state: SharedFilterState,
        theme: Callable[[], Theme],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("filterBar")
        self._state = state
        self._theme = theme
        self._edits: dict[FilterField, QLineEdit] = {}
        self._sport_actions: dict[Sport, QAction] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE_4, SPACE_2, SPACE_4, SPACE_2)
        outer.setSpacing(SPACE_1)
        row = QHBoxLayout()
        row.setSpacing(SPACE_4)
        for kind, first, second in _GROUPS:
            row.addWidget(self._group(kind, first, second))
        row.addWidget(self._sport_group())
        row.addStretch(1)
        self._clear = QPushButton(self.tr("Clear filters"), self)
        self._clear.setToolTip(self.tr("Show every activity again"))
        self._clear.clicked.connect(self.clear_filters)
        row.addWidget(self._clear, alignment=Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(row)
        self._problems = QLabel(self)
        self._problems.setObjectName("filterProblems")
        self._problems.setTextFormat(Qt.TextFormat.PlainText)
        self._problems.setWordWrap(True)
        self._problems.setAccessibleName(self.tr("Filter problems"))
        self._problems.hide()
        outer.addWidget(self._problems)
        self._show(state.displayed())
        state.changed.connect(self._on_state_changed)

    # --- Parts -------------------------------------------------------------------------

    def _group(self, kind: str, first: FilterField, second: FilterField) -> QWidget:
        box = QWidget(self)
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(SPACE_1)
        title = QLabel(_group_title(kind), box)
        grid.addWidget(title, 0, 0, 1, 2)
        for column, field in enumerate((first, second)):
            edit = QLineEdit(box)
            edit.setObjectName(f"filter_{_NAME_OF[field]}")
            edit.setMinimumWidth(_FIELD_MIN_WIDTH)
            edit.setAccessibleName(field_label(field))
            edit.setPlaceholderText(self._placeholder(field))
            edit.editingFinished.connect(self.apply_fields)
            if column == 0:
                title.setBuddy(edit)
            grid.addWidget(edit, 1, column)
            self._edits[field] = edit
        return box

    def _placeholder(self, field: FilterField) -> str:
        if field in {FilterField.DATE_FROM, FilterField.DATE_TO}:
            example = QLocale().toString(QDate(2026, 3, 5), QLocale.FormatType.ShortFormat)
            return self.tr("e.g. {example}").format(example=example)
        return self.tr("from") if field.value.endswith("Min") else self.tr("to")

    def _sport_group(self) -> QWidget:
        box = QWidget(self)
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(SPACE_1)
        column.addWidget(QLabel(self.tr("Sport"), box))
        self._sports_button = QToolButton(box)
        self._sports_button.setObjectName("filterSports")
        self._sports_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._sports_button.setAccessibleName(self.tr("Sports"))
        menu = QMenu(self._sports_button)
        for sport in Sport:
            action = menu.addAction(sport_text(sport))
            action.setCheckable(True)
            action.toggled.connect(self.apply_fields)
            self._sport_actions[sport] = action
        self._sports_button.setMenu(menu)
        column.addWidget(self._sports_button)
        self._update_sports_text()
        return box

    # --- Reading and showing ---------------------------------------------------------------

    @property
    def edits(self) -> dict[FilterField, QLineEdit]:
        """The text fields (for tests and for focusing)."""
        return self._edits

    @property
    def sport_actions(self) -> dict[Sport, QAction]:
        """The checkable sport entries."""
        return self._sport_actions

    @property
    def problems_label(self) -> QLabel:
        """The label that says, in words, what is wrong."""
        return self._problems

    def focus_first_field(self) -> None:
        """Put the cursor in the first field (the "Filter activities" command)."""
        self._edits[FilterField.DATE_FROM].setFocus(Qt.FocusReason.ShortcutFocusReason)

    def texts(self) -> FilterTexts:
        """What the fields hold now."""
        values = {field: edit.text() for field, edit in self._edits.items()}
        chosen = frozenset(s for s, action in self._sport_actions.items() if action.isChecked())
        return FilterTexts.from_fields(values, chosen)

    def apply_fields(self) -> None:
        """Hand the fields to the shared state; mark and explain what is wrong."""
        self._update_sports_text()
        outcome = self._state.apply(self.texts())
        self._mark(outcome.problems)

    def clear_filters(self) -> None:
        """No filter at all."""
        self._state.clear()
        self._show(self._state.displayed())

    def _on_state_changed(self) -> None:
        self._show(self._state.displayed())

    def _show(self, texts: FilterTexts) -> None:
        for field, edit in self._edits.items():
            edit.blockSignals(True)  # showing a value is not an edit
            edit.setText(texts.text_of(field))
            edit.blockSignals(False)
        for sport, action in self._sport_actions.items():
            action.blockSignals(True)
            action.setChecked(sport in texts.sports)
            action.blockSignals(False)
        self._update_sports_text()
        self._mark({})

    def _update_sports_text(self) -> None:
        chosen = [s for s in Sport if self._sport_actions[s].isChecked()]
        if not chosen:
            self._sports_button.setText(self.tr("All sports"))
        elif len(chosen) == 1:
            self._sports_button.setText(sport_text(chosen[0]))
        else:
            self._sports_button.setText(self.tr("%n sports", "", len(chosen)))

    def _mark(self, wrong: Mapping[FilterField, FieldProblem]) -> None:
        """Mark wrong fields (border and tooltip) and list what is wrong, in words."""
        error = tokens_for(self._theme()).error
        messages: list[str] = []
        for field, edit in self._edits.items():
            problem = wrong.get(field)
            if problem is None:
                edit.setStyleSheet("")
                edit.setToolTip("")
                edit.setAccessibleDescription("")
                continue
            text = problem_text(field, problem)
            messages.append(text)
            edit.setStyleSheet(f"QLineEdit {{ border: 2px solid {error}; }}")
            edit.setToolTip(text)
            edit.setAccessibleDescription(text)
        self._problems.setText("\n".join(messages))
        self._problems.setVisible(bool(messages))
