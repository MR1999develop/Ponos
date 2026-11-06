# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Finestra "Dettaglio attività": riepilogo, grafico della FC con le zone e tempo per zona.

La finestra non è modale: l'utente può guardarla accanto alla panoramica. Mostra uno di tre
stati: caricamento, esito del caso d'uso (dettaglio o motivo per cui il file non si usa) o errore.
Le figure arrivano già preparate dal thread dei dati.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QDateTime,
    QLocale,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    Qt,
    QTime,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.load.activity_detail import ActivityDetail
from ponos_coach.ui.activity_detail import (
    DetailOutcome,
    DetailReady,
    DetailUnavailable,
    ZoneRow,
    zone_rows,
)
from ponos_coach.ui.activity_text import (
    ascent_m_text,
    distance_km_text,
    duration_text,
    load_source_text,
    sport_text,
)
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.errors import ErrorMessage, unavailable_detail_message
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.tokens import SPACE_2, SPACE_4

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_PERCENT = 100
_DEFAULT_SIZE = (860, 700)
_CHART_MIN_HEIGHT = 280
_ZONE_TABLE_HEIGHT = 170


class ZoneTableModel(QAbstractTableModel):
    """Tempo per zona di un'attività: zona, tempo, quota. Sola lettura, numeri a destra."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ZoneRow, ...] = ()

    def set_rows(self, rows: tuple[ZoneRow, ...]) -> None:
        """Sostituisce le righe."""
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Una riga per zona."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Zona, tempo, quota."""
        if parent is not None and parent.isValid():
            return 0
        return 3

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Intestazioni, con l'unità."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < 3:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                self.tr("Zone (% of threshold)"),
                self.tr("Time (h:mm:ss)"),
                self.tr("Share of the time in zones (%)"),
            )[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if section == 0 else _ALIGN_RIGHT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Testo e allineamento di una cella."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if column == 0 else _ALIGN_RIGHT
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if column == 0:
            return row.label
        if column == 1:
            return duration_text(row.seconds)
        return QLocale().toString(round(row.share * _PERCENT))


class ActivityDetailDialog(QDialog):
    """Riepilogo, grafico della FC e tempo per zona di una attività."""

    def __init__(self, engine: ChartEngine, zone: ZoneInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zone = zone
        self.setWindowTitle(self.tr("Activity detail"))
        self.setModal(False)
        self.resize(*_DEFAULT_SIZE)
        self._chart = ChartView(engine, self.tr("Heart rate chart"), self)
        self._chart.setMinimumHeight(_CHART_MIN_HEIGHT)
        self._zone_model = ZoneTableModel(self)
        self._build()
        self.finished.connect(self._chart.dispose)
        self.show_loading()

    # --- Costruzione ------------------------------------------------------------------

    def _build(self) -> None:
        self._title = QLabel(self)
        self._title.setObjectName("detailTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        self._summary = QFormLayout()
        self._summary.setVerticalSpacing(SPACE_2)
        self._summary_values: dict[str, QLabel] = {}
        for key, text in self._summary_rows():
            label = QLabel(self)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setObjectName(f"detail-{key}")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._summary_values[key] = label
            self._summary.addRow(text, label)
        self._notice = QLabel(self)
        self._notice.setObjectName("detailNotice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)

        self._table = QTableView(self)
        self._table.setObjectName("detailZones")
        self._table.setAccessibleName(self.tr("Time in each zone"))
        self._table.setModel(self._zone_model)
        self._table.setShowGrid(False)  # niente linee verticali né orizzontali
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setMaximumHeight(_ZONE_TABLE_HEIGHT)

        self._message = StatePage(self)
        self._message.button.setVisible(False)
        self._body = QStackedWidget(self)
        self._body.addWidget(self._message)
        self._body.addWidget(self._chart)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._title)
        outer.addLayout(self._summary)
        outer.addWidget(self._notice)
        outer.addWidget(self._body, stretch=1)
        outer.addWidget(self._table)
        outer.addWidget(buttons)

    def _summary_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("start", self.tr("Started")),
            ("moving", self.tr("Moving time (h:mm:ss)")),
            ("elapsed", self.tr("Elapsed time (h:mm:ss)")),
            ("distance", self.tr("Distance (km)")),
            ("ascent", self.tr("Ascent (m)")),
            ("load", self.tr("Load (hrTSS)")),
            ("source", self.tr("Load from")),
            ("threshold", self.tr("Threshold heart rate (bpm)")),
            ("limits", self.tr("Heart-rate limits (bpm)")),
        )

    # --- Accessori per i test ---------------------------------------------------------

    @property
    def chart(self) -> ChartView:
        """Il grafico della FC."""
        return self._chart

    @property
    def zone_model(self) -> ZoneTableModel:
        """Il modello della tabella delle zone."""
        return self._zone_model

    @property
    def title_text(self) -> str:
        """Titolo: sport e data."""
        return self._title.text()

    @property
    def message_page(self) -> StatePage:
        """La pagina dei messaggi (caricamento, assenza di FC, file non disponibile, errore)."""
        return self._message

    @property
    def notice_text(self) -> str:
        """Avviso sui campioni scartati (vuoto se non c'è)."""
        return self._notice.text() if self._notice.isVisible() else ""

    def summary_text(self, key: str) -> str:
        """Il valore di una riga del riepilogo."""
        return self._summary_values[key].text()

    def shows_chart(self) -> bool:
        """Vero se il grafico è la pagina visibile."""
        return self._body.currentWidget() is self._chart

    # --- Stati ------------------------------------------------------------------------

    def show_loading(self) -> None:
        """Mentre il thread dei dati lavora."""
        self._set_details_visible(False)
        self._show_message(self.tr("Loading the activity..."), "")

    def show_error(self, message: ErrorMessage) -> None:
        """Un errore imprevisto del caricamento."""
        self._set_details_visible(False)
        self._show_message(message.title, f"{message.what}\n\n{message.remedy}")

    def show_outcome(self, outcome: DetailOutcome) -> None:
        """Mostra il dettaglio, o il motivo per cui il file non si può usare."""
        if isinstance(outcome, DetailUnavailable):
            message = unavailable_detail_message(outcome.reason)
            self._set_details_visible(False)
            self._show_message(message.title, f"{message.what}\n\n{message.remedy}")
            return
        self._show_detail(outcome)

    def _show_detail(self, ready: DetailReady) -> None:
        detail = ready.detail
        self._set_details_visible(True)
        self._fill_summary(detail)
        self._zone_model.set_rows(zone_rows(detail))
        self._table.setVisible(ready.chart is not None)  # senza FC il carico è stimato: niente zone
        if ready.chart is None:
            self._show_message(
                self.tr("No heart-rate data"),
                self.tr("This activity has no heart-rate samples, so its load is estimated."),
            )
        else:
            self._chart.show_prepared(ready.chart)
            self._body.setCurrentWidget(self._chart)

    def _fill_summary(self, detail: ActivityDetail) -> None:
        local = detail.start.astimezone(self._zone)
        started = QLocale().toString(
            QDateTime(
                QDate(local.year, local.month, local.day),
                QTime(local.hour, local.minute, local.second),
            ),
            QLocale.FormatType.ShortFormat,
        )
        sport = sport_text(detail.sport)
        self._title.setText(self.tr("{sport}, {started}").format(sport=sport, started=started))
        limits = detail.limits
        values = {
            "start": started,
            "moving": duration_text(detail.timer_s),
            "elapsed": duration_text(detail.elapsed_s),
            "distance": distance_km_text(detail.summary.distance_m),
            "ascent": ascent_m_text(detail.summary.ascent_m),
            "load": QLocale().toString(round(detail.load.load)),
            "source": load_source_text(detail.load.source),
            "threshold": QLocale().toString(detail.threshold_bpm),
            "limits": self.tr("{low} to {high}").format(low=limits.min_bpm, high=limits.max_bpm),
        }
        for key, text in values.items():
            self._summary_values[key].setText(text)
        self._notice.setText(self._notice_text(detail))
        self._notice.setVisible(bool(self._notice.text()))

    def _notice_text(self, detail: ActivityDetail) -> str:
        notes = []
        discarded = detail.load.discarded_samples
        if discarded:
            notes.append(
                self.tr(
                    "Heart-rate samples outside your limits: %n. They were not used and are "
                    "marked in the chart.",
                    "",
                    discarded,
                )
            )
        if detail.unordered_hr_samples:
            notes.append(
                self.tr(
                    "Heart-rate samples skipped when the file was read because their time was "
                    "repeated or earlier: %n.",
                    "",
                    detail.unordered_hr_samples,
                )
            )
        return " ".join(notes)

    def _set_details_visible(self, visible: bool) -> None:
        for label in self._summary_values.values():
            label.setVisible(visible)
        self._table.setVisible(visible)
        if not visible:
            self._notice.setVisible(False)
            self._title.setText("")

    def _show_message(self, title: str, text: str) -> None:
        self._message.set_content(title, text, "")
        self._body.setCurrentWidget(self._message)
