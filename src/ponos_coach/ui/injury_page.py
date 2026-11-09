# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page "Injury history": the notices, the table of the records and the state of the selected one.

Humble view: the notices, the sentences and the table come ready from `injury_presentation`,
`injury_texts` and `injury_table`. A notice always opens with a word ("See a doctor", "Plan"), and
its colour is only a border: the meaning never rests on the colour alone. The page only shows:
the injuries are reported and followed on the days of the calendar. It is a widget of its own, so
that the settings window can take it in later. Health data: shown here and never logged.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QModelIndex, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.injury_presentation import HealthNotice, ProblemSummary
from ponos_coach.ui.injury_table import (
    InjuryProxy,
    InjuryTableModel,
    sorted_injuries_model,
)
from ponos_coach.ui.injury_texts import detail_lines, notice_prefix, notice_texts
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_3, SPACE_4, Theme, warning_color_for

_BORDER_WIDTH = 4


class InjuriesPage(QWidget):
    """Notices, table of injuries and illnesses, and the state of the selected one."""

    selection_changed = Signal(object)  # str | None: the id of the record selected
    problem_activated = Signal(str)  # Enter or double click on a record

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = InjuryTableModel(self)
        self._proxy: InjuryProxy = sorted_injuries_model(self._model, self)
        self._notice_widgets: list[QWidget] = []
        self._build()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        self._notices = QVBoxLayout()
        self._notices.setSpacing(SPACE_2)
        self._table = QTableView(self)
        self._table.setObjectName("injuriesTable")
        self._table.setAccessibleName(self.tr("Injuries and illnesses"))
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(1, Qt.SortOrder.DescendingOrder)  # the most recent first
        self._table.setShowGrid(False)  # no vertical or horizontal lines
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self._table.activated.connect(self._on_activated)
        self._empty = QLabel(self)
        self._empty.setObjectName("injuriesEmpty")
        self._empty.setTextFormat(Qt.TextFormat.PlainText)
        self._empty.setWordWrap(True)
        self._empty.setText(
            self.tr(
                "No injuries or illnesses. If something hurts or you do not feel well, "
                "choose Report an injury or Report illness, and the plan adapts."
            )
        )
        self._detail = QLabel(self)
        self._detail.setObjectName("injuryDetail")
        self._detail.setAccessibleName(self.tr("State of the selected problem"))
        self._detail.setTextFormat(Qt.TextFormat.PlainText)
        self._detail.setWordWrap(True)
        self._detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_3)
        layout.addLayout(self._notices)
        layout.addWidget(self._empty)
        layout.addWidget(self._table, stretch=1)
        layout.addWidget(self._detail)

    # --- State for the tests ---------------------------------------------------------------

    @property
    def table(self) -> QTableView:
        """The records table view."""
        return self._table

    @property
    def model(self) -> InjuryTableModel:
        """The records model."""
        return self._model

    @property
    def proxy(self) -> InjuryProxy:
        """The sorting proxy."""
        return self._proxy

    @property
    def notice_texts(self) -> tuple[str, ...]:
        """The text of each notice shown (prefix, sentence and remedy), top to bottom."""
        return tuple(
            "\n".join(
                label.text()
                for label in widget.findChildren(QLabel)
                if label.objectName() in {"noticePrefix", "noticeHeadline", "noticeRemedy"}
            )
            for widget in self._notice_widgets
        )

    @property
    def empty_text(self) -> str:
        """The empty-state sentence (empty while there are records)."""
        return "" if self._empty.isHidden() else self._empty.text()

    @property
    def detail_text(self) -> str:
        """The state of the selected record, one sentence a line (empty if none is selected)."""
        return self._detail.text()

    def selected_problem_id(self) -> str | None:
        """The id of the selected record, if any."""
        rows = self._table.selectionModel().selectedRows()
        return self._proxy.problem_id_at(rows[0].row()) if rows else None

    def select_problem(self, problem_id: str) -> None:
        """Select the row of a record."""
        for row in range(self._proxy.rowCount()):
            if self._proxy.problem_id_at(row) == problem_id:
                self._table.selectRow(row)
                return

    # --- Showing the state -----------------------------------------------------------------

    def show_state(
        self, items: Sequence[ProblemSummary], notices: Sequence[HealthNotice], theme: Theme
    ) -> None:
        """Show the records and the notices; the selection stays on the same record."""
        selected = self.selected_problem_id()
        self._model.set_items(items)
        self._empty.setVisible(not items)
        self._table.setVisible(bool(items))
        for widget in self._notice_widgets:
            self._notices.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._notice_widgets = []
        for notice in notices:
            widget = self._notice_widget(notice, theme)
            self._notices.addWidget(widget)
            self._notice_widgets.append(widget)
        if selected is not None:
            self.select_problem(selected)
        self._show_detail()
        self.selection_changed.emit(self.selected_problem_id())

    def selected_summary(self) -> ProblemSummary | None:
        """The summary of the selected record, if any."""
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._model.item_at(self._proxy.mapToSource(rows[0]).row())

    def _show_detail(self) -> None:
        item = self.selected_summary()
        self._detail.setText("" if item is None else "\n".join(detail_lines(item)))
        self._detail.setVisible(item is not None)

    def _notice_widget(self, notice: HealthNotice, theme: Theme) -> QWidget:
        headline, remedy = notice_texts(notice)
        frame = QFrame(self)
        frame.setObjectName("injuryNotice")
        frame.setStyleSheet(
            f"QFrame#injuryNotice {{ border-left: {_BORDER_WIDTH}px solid "
            f"{warning_color_for(theme)}; }}"
        )
        prefix = QLabel(notice_prefix(notice), frame)
        prefix.setObjectName("noticePrefix")
        prefix.setTextFormat(Qt.TextFormat.PlainText)
        font = prefix.font()
        font.setBold(True)
        prefix.setFont(font)
        head = QLabel(headline, frame)
        head.setObjectName("noticeHeadline")
        head.setTextFormat(Qt.TextFormat.PlainText)
        head.setWordWrap(True)
        text = QVBoxLayout(frame)
        text.setContentsMargins(SPACE_3, SPACE_1, SPACE_1, SPACE_1)
        text.setSpacing(SPACE_1)
        text.addWidget(prefix)
        text.addWidget(head)
        if remedy:
            fix = QLabel(remedy, frame)
            fix.setObjectName("noticeRemedy")
            fix.setTextFormat(Qt.TextFormat.PlainText)
            fix.setWordWrap(True)
            text.addWidget(fix)
        return frame

    def _on_selection_changed(self) -> None:
        self._show_detail()
        self.selection_changed.emit(self.selected_problem_id())

    def _on_activated(self, index: QModelIndex) -> None:
        problem_id = self._proxy.problem_id_at(index.row())
        if problem_id is not None:
            self.problem_activated.emit(problem_id)
