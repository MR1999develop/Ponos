# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Riepilogo dell'import: conteggi per esito e tabella dei file non importati col motivo."""

from __future__ import annotations

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.errors import import_issue_text
from ponos_coach.ui.import_report import ImportReport, NotImported
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4


class NotImportedModel(QAbstractTableModel):
    """File non importati: nome e motivo in parole comuni."""

    def __init__(self, rows: tuple[NotImported, ...], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows = rows

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Numero di file."""
        return 0 if parent is not None and parent.isValid() else len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Due colonne: file e motivo."""
        return 0 if parent is not None and parent.isValid() else 2

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Intestazioni."""
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.tr("File") if section == 0 else self.tr("Reason")
        return None

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        """Testo di una cella (i nomi sono sempre testo semplice)."""
        if not index.isValid() or role != Qt.ItemDataRole.DisplayRole:
            return None
        row = self._rows[index.row()]
        return row.name if index.column() == 0 else import_issue_text(row.issue)


class ImportReportDialog(QDialog):
    """Mostra l'esito di un import."""

    def __init__(self, report: ImportReport, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            self.tr("Import stopped") if report.cancelled else self.tr("Import finished")
        )
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self._report = report
        layout = QVBoxLayout(self)
        layout.setSpacing(SPACE_2)
        self._summary = QLabel("\n".join(self.summary_lines(report)), self)
        self._summary.setObjectName("importSummary")
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._summary)
        self._table = QTableView(self)
        self._table.setObjectName("notImportedTable")
        self._table.setAccessibleName(self.tr("Files not imported"))
        self._model = NotImportedModel(report.not_imported, self)
        self._table.setModel(self._model)
        self._table.setShowGrid(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self._table.setVisible(bool(report.not_imported))
        if report.not_imported:
            heading = QLabel(self.tr("Files not imported"), self)
            layout.addSpacing(SPACE_4)
            layout.addWidget(heading)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(self)
        close = buttons.addButton(self.tr("Close"), QDialogButtonBox.ButtonRole.AcceptRole)
        close.setDefault(True)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @property
    def table(self) -> QTableView:
        """La tabella dei file non importati."""
        return self._table

    @property
    def summary_text(self) -> str:
        """Il testo dei conteggi."""
        return self._summary.text()

    def summary_lines(self, report: ImportReport) -> list[str]:
        """Una riga per ogni esito presente (l'importate sempre)."""
        lines = [self.tr("%n activity(ies) imported", "", report.imported)]
        if report.already_present:
            lines.append(self.tr("%n already in the profile", "", report.already_present))
        if report.ignored:
            lines.append(self.tr("%n file(s) ignored (not an activity)", "", report.ignored))
        if report.failed:
            lines.append(self.tr("%n file(s) with an error", "", report.failed))
        if report.cancelled:
            lines.append(self.tr("The import was stopped: run it again to continue."))
        return lines
