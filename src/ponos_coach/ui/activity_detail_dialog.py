# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Window "Activity detail": summary, HR chart with zones, speed or pace chart and time per
zone.

The window is not modal: the user can keep it next to the overview. It shows one of three
states: loading, the use case outcome (detail or the reason the file is not usable) or error.
The figures arrive already prepared by the data thread.
"""

from __future__ import annotations

from collections.abc import Callable
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
    Signal,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.activities.domain import ActivityIdentity
from ponos_coach.load.activity_detail import ActivityDetail
from ponos_coach.nutrition.domain import ActivityEnergy
from ponos_coach.ui.activity_detail import (
    PACE_SPORTS,
    DetailOutcome,
    DetailReady,
    DetailUnavailable,
    DomainKind,
    DomainRow,
    ZoneRow,
    average_pace_s_per_km,
    domain_rows,
    zone_rows,
)
from ponos_coach.ui.activity_text import (
    active_calories_caption,
    active_calories_text,
    ascent_m_text,
    distance_km_text,
    duration_text,
    load_source_text,
    pace_text,
    sport_text,
)
from ponos_coach.ui.chart_view import ChartEngine, ChartView
from ponos_coach.ui.errors import ErrorMessage, unavailable_detail_message
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.threshold_presentation import can_be_threshold_test, not_a_run_reason
from ponos_coach.ui.tokens import SPACE_2, SPACE_4, Theme

_ALIGN_RIGHT = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
_ALIGN_LEFT = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
_PERCENT = 100
_ONE_DECIMAL_BELOW = 10.0
_DEFAULT_SIZE = (940, 900)
_CHART_MIN_HEIGHT = 190  # the plot area keeps about 90 px between the legend and the axis
_SUMMARY_PAIRS_PER_ROW = 3  # caption and value, three times on a row
_ZONES_STRETCH = 5  # the zones table has five columns, the domains table four
_DOMAINS_STRETCH = 4
_ALIGN_TOP = Qt.AlignmentFlag.AlignTop


_COLUMNS = 5  # zone, heart rate, time, share, load
_BPM_COLUMN = 1
_TIME_COLUMN = 2
_SHARE_COLUMN = 3
_EN_DASH = "–"  # noqa: RUF001 - the range separator is the character itself


class ZoneTableModel(QAbstractTableModel):
    """Heart rate, time and load per zone of an activity: zone, heart rate, time, share, load.
    Read-only, numbers on the right."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[ZoneRow, ...] = ()

    def set_rows(self, rows: tuple[ZoneRow, ...]) -> None:
        """Replace the rows."""
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """One row per zone."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Zone, heart rate, time, share, load."""
        if parent is not None and parent.isValid():
            return 0
        return _COLUMNS

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Headers, with the unit."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < _COLUMNS:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                self.tr("Zone (% of threshold)"),
                self.tr("Heart rate (bpm)"),
                self.tr("Time (h:mm:ss)"),
                self.tr("Share of the time in zones (%)"),
                self.tr("Load (TRIMPeq)"),
            )[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if section == 0 else _ALIGN_RIGHT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text and alignment of a cell."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if column == 0 else _ALIGN_RIGHT
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return _cell_text(row, column)


_DOMAIN_COLUMNS = 4  # domain, heart rate, time, share


class DomainTableModel(QAbstractTableModel):
    """Heart rate and time below LT1, between LT1 and LT2 and above LT2: domain, heart rate,
    time, share. Read-only, numbers on the right."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[DomainRow, ...] = ()

    def set_rows(self, rows: tuple[DomainRow, ...]) -> None:
        """Replace the rows."""
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """One row per domain."""
        if parent is not None and parent.isValid():
            return 0
        return len(self._rows)

    def columnCount(  # noqa: N802 - Qt
        self, parent: QModelIndex | QPersistentModelIndex | None = None
    ) -> int:
        """Domain, heart rate, time, share."""
        if parent is not None and parent.isValid():
            return 0
        return _DOMAIN_COLUMNS

    def headerData(  # noqa: N802 - Qt
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Headers, with the unit."""
        if orientation != Qt.Orientation.Horizontal or not 0 <= section < _DOMAIN_COLUMNS:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return (
                self.tr("Intensity"),
                self.tr("Heart rate (bpm)"),
                self.tr("Time (h:mm:ss)"),
                self.tr("Share of the time (%)"),
            )[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if section == 0 else _ALIGN_RIGHT
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Text and alignment of a cell."""
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGN_LEFT if column == 0 else _ALIGN_RIGHT
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        return self._cell_text(row, column)

    def _cell_text(self, row: DomainRow, column: int) -> str:
        if column == 0:
            return self._domain_name(row.kind)
        if column == _BPM_COLUMN:
            return _bpm_range_text(row.bpm)
        if column == _TIME_COLUMN:
            return duration_text(row.seconds)
        return QLocale().toString(round(row.share * _PERCENT))

    def _domain_name(self, kind: DomainKind) -> str:
        return {
            DomainKind.BELOW_LT1: self.tr("Below LT1"),
            DomainKind.BETWEEN: self.tr("Between LT1 and LT2"),
            DomainKind.ABOVE_LT2: self.tr("Above LT2"),
        }[kind]


def _cell_text(row: ZoneRow, column: int) -> str:
    if column == 0:
        return row.label
    if column == _BPM_COLUMN:
        return _bpm_range_text(row.bpm)
    if column == _TIME_COLUMN:
        return duration_text(row.seconds)
    if column == _SHARE_COLUMN:
        return QLocale().toString(round(row.share * _PERCENT))
    return _zone_load_text(row.load)


def _bpm_range_text(bpm: tuple[int, int] | None) -> str:
    """Heart-rate range of a zone, whole bpm, ends included, joined by an en dash; empty if none."""
    if bpm is None:
        return ""
    locale = QLocale()
    return f"{locale.toString(bpm[0])}{_EN_DASH}{locale.toString(bpm[1])}"


def _zone_load_text(load: float | None) -> str:
    """Load earned in a zone, one decimal under 10 (short sessions); empty for an estimate."""
    if load is None:
        return ""
    if load < _ONE_DECIMAL_BELOW:
        return QLocale().toString(load, "f", 1)
    return QLocale().toString(round(load))


class ActivityDetailDialog(QDialog):
    """Summary, HR chart, speed or pace chart and time per zone of an activity."""

    threshold_test_requested = Signal(object)  # ActivityIdentity

    def __init__(
        self,
        engine: ChartEngine,
        zone: ZoneInfo,
        parent: QWidget | None = None,
        *,
        theme: Callable[[], Theme],
    ) -> None:
        super().__init__(parent)
        self._zone = zone
        self._theme = theme
        self._identity: ActivityIdentity | None = None
        self.setWindowTitle(self.tr("Activity detail"))
        self.setModal(False)
        self.resize(*_DEFAULT_SIZE)
        self._chart = ChartView(engine, self.tr("Heart rate chart"), self)
        self._chart.setMinimumHeight(_CHART_MIN_HEIGHT)
        self._speed_chart = ChartView(engine, self.tr("Speed chart"), self)
        self._speed_chart.setMinimumHeight(_CHART_MIN_HEIGHT)
        # one time axis for both charts: zooming or panning one moves the other
        self._chart.x_range_changed.connect(self._speed_chart.set_x_range)
        self._speed_chart.x_range_changed.connect(self._chart.set_x_range)
        self._zone_model = ZoneTableModel(self)
        self._domain_model = DomainTableModel(self)
        self._build()
        self.finished.connect(self._chart.dispose)
        self.finished.connect(self._speed_chart.dispose)
        self.show_loading()

    # --- Costruzione ------------------------------------------------------------------

    def _build(self) -> None:
        self._title = QLabel(self)
        self._title.setObjectName("detailTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        self._build_summary()
        self._notice = QLabel(self)
        self._notice.setObjectName("detailNotice")
        self._notice.setTextFormat(Qt.TextFormat.PlainText)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)

        self._build_table()

        self._message = StatePage(self)
        self._message.button.setVisible(False)
        self._body = QStackedWidget(self)
        self._charts = QWidget(self)
        charts_layout = QVBoxLayout(self._charts)
        charts_layout.setContentsMargins(0, 0, 0, 0)
        charts_layout.setSpacing(SPACE_2)
        charts_layout.addWidget(self._chart, stretch=1)
        charts_layout.addWidget(self._speed_chart, stretch=1)
        self._body.addWidget(self._message)
        self._body.addWidget(self._charts)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        self._use_as_test = buttons.addButton(
            self.tr("Use as threshold test..."), QDialogButtonBox.ButtonRole.ActionRole
        )
        self._use_as_test.setObjectName("detailUseAsTest")
        self._use_as_test.clicked.connect(self._on_use_as_test)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._title)
        outer.addLayout(self._summary)
        outer.addWidget(self._notice)
        outer.addWidget(self._body, stretch=1)
        tables = QHBoxLayout()
        tables.setSpacing(SPACE_4)
        tables.addWidget(self._table, stretch=_ZONES_STRETCH, alignment=_ALIGN_TOP)
        tables.addWidget(self._domain_table, stretch=_DOMAINS_STRETCH, alignment=_ALIGN_TOP)
        outer.addLayout(tables)
        outer.addWidget(buttons)

    def _build_table(self) -> None:
        self._table = self._plain_table(
            self._zone_model, "detailZones", self.tr("Time in each zone")
        )
        self._domain_table = self._plain_table(
            self._domain_model,
            "detailDomains",
            self.tr("Time below LT1, between LT1 and LT2 and above LT2"),
        )

    def _plain_table(self, model: QAbstractTableModel, name: str, accessible: str) -> QTableView:
        """A small read-only table, as tall as its rows: no scroll bar, no grid lines."""
        table = QTableView(self)
        table.setObjectName(name)
        table.setAccessibleName(accessible)
        table.setModel(model)
        table.setShowGrid(False)  # no vertical or horizontal lines
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QTableView.SelectionMode.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return table

    @staticmethod
    def _fit_height(table: QTableView) -> None:
        """Height from the content (header, rows, frame): every row is visible, no scroll bar."""
        model = table.model()
        rows = sum(table.rowHeight(row) for row in range(model.rowCount()))
        table.setFixedHeight(table.horizontalHeader().height() + rows + 2 * table.frameWidth())

    def _fit_table_height(self) -> None:
        self._fit_height(self._table)
        self._fit_height(self._domain_table)

    def _build_summary(self) -> None:
        """The summary values as a grid of caption and value pairs, three pairs per row."""
        self._summary = QGridLayout()
        self._summary.setVerticalSpacing(SPACE_2)
        self._summary.setHorizontalSpacing(SPACE_4)
        self._summary_values: dict[str, QLabel] = {}
        self._summary_captions: dict[str, QLabel] = {}
        for key, text in self._summary_rows():
            caption = QLabel(text, self)
            caption.setTextFormat(Qt.TextFormat.PlainText)
            value = QLabel(self)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setObjectName(f"detail-{key}")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._summary_captions[key] = caption
            self._summary_values[key] = value
        for pair in range(_SUMMARY_PAIRS_PER_ROW):
            self._summary.setColumnStretch(2 * pair + 1, 1)
        self._summary_shown: tuple[str, ...] = ()
        self._lay_out_summary(tuple(self._summary_values))

    def _lay_out_summary(self, keys: tuple[str, ...]) -> None:
        """Place the pairs of `keys` in order, with no gap where a pair is left out (the pace of
        a sport not measured by pace)."""
        if keys == self._summary_shown:
            return
        for key in self._summary_values:
            self._summary.removeWidget(self._summary_captions[key])
            self._summary.removeWidget(self._summary_values[key])
        for position, key in enumerate(keys):
            row, pair = divmod(position, _SUMMARY_PAIRS_PER_ROW)
            self._summary.addWidget(self._summary_captions[key], row, 2 * pair)
            self._summary.addWidget(self._summary_values[key], row, 2 * pair + 1)
        self._summary_shown = keys

    def _summary_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("start", self.tr("Started")),
            ("moving", self.tr("Moving time (h:mm:ss)")),
            ("elapsed", self.tr("Elapsed time (h:mm:ss)")),
            ("distance", self.tr("Distance (km)")),
            ("pace", self.tr("Average pace (min/km)")),
            ("ascent", self.tr("Ascent (m)")),
            ("calories", active_calories_caption()),
            ("load", self.tr("Load (TRIMPeq)")),
            ("source", self.tr("Load from")),
            ("thresholds", self.tr("LT1 / LT2 (bpm)")),
            ("limits", self.tr("Heart-rate limits (bpm)")),
        )

    def warm_up_chart(self) -> None:
        """Create the chart's web page now, while the data thread is still working, so the page
        is loaded when the figure arrives. Harmless if the window is already closed."""
        self._chart.start_engine()

    # --- Test accessors ---------------------------------------------------------------

    @property
    def chart(self) -> ChartView:
        """The HR chart."""
        return self._chart

    @property
    def speed_chart(self) -> ChartView:
        """The speed chart (a pace chart for the sports measured by pace)."""
        return self._speed_chart

    def shows_speed_chart(self) -> bool:
        """True if the speed or pace chart is visible."""
        return self.shows_chart_page() and self._speed_chart.isVisibleTo(self)

    def shows_chart_page(self) -> bool:
        """True if the charts are the visible page (some activity data is drawn)."""
        return self._body.currentWidget() is self._charts

    @property
    def zone_model(self) -> ZoneTableModel:
        """The zones table model."""
        return self._zone_model

    @property
    def domain_model(self) -> DomainTableModel:
        """The model of the time below LT1, between LT1 and LT2 and above LT2."""
        return self._domain_model

    @property
    def domain_table(self) -> QTableView:
        """The view of the three domains."""
        return self._domain_table

    @property
    def zone_table(self) -> QTableView:
        """The zones table view."""
        return self._table

    def summary_value_widget(self, key: str) -> QLabel:
        """The label that holds the value of one summary row."""
        return self._summary_values[key]

    @property
    def title_text(self) -> str:
        """Title: sport and date."""
        return self._title.text()

    @property
    def message_page(self) -> StatePage:
        """The messages page (loading, no HR, file unavailable, error)."""
        return self._message

    @property
    def notice_text(self) -> str:
        """Notice about discarded samples (empty if there are none)."""
        return self._notice.text() if self._notice.isVisible() else ""

    def summary_text(self, key: str) -> str:
        """The value of one summary row."""
        return self._summary_values[key].text()

    @property
    def use_as_test_button(self) -> QPushButton:
        """The "Use as threshold test..." button."""
        return self._use_as_test

    def shows_chart(self) -> bool:
        """True if the HR chart is visible."""
        return self.shows_chart_page() and self._chart.isVisibleTo(self)

    # --- Stati ------------------------------------------------------------------------

    def show_loading(self) -> None:
        """While the data thread is working."""
        self._set_test_command(None, self.tr("The activity is still loading."))
        self._set_details_visible(False)
        self._show_message(self.tr("Loading the activity..."), "")

    def show_error(self, message: ErrorMessage) -> None:
        """An unexpected loading error."""
        self._set_test_command(None, self.tr("The activity detail is not available."))
        self._set_details_visible(False)
        self._show_message(message.title, f"{message.what}\n\n{message.remedy}")

    def show_outcome(self, outcome: DetailOutcome) -> None:
        """Show the detail, or the reason the file cannot be used."""
        if isinstance(outcome, DetailUnavailable):
            message = unavailable_detail_message(outcome.reason)
            self._set_test_command(None, self.tr("The activity detail is not available."))
            self._set_details_visible(False)
            self._show_message(message.title, f"{message.what}\n\n{message.remedy}")
            return
        self._show_detail(outcome)

    def show_outcome_of(self, outcome: object) -> None:
        """Slot for the loader's `loaded` signal (it carries an `object`)."""
        if isinstance(outcome, DetailReady | DetailUnavailable):
            self.show_outcome(outcome)

    def show_error_of(self, message: object) -> None:
        """Slot for the loader's `failed` signal (it carries an `object`)."""
        if isinstance(message, ErrorMessage):
            self.show_error(message)

    def _show_detail(self, ready: DetailReady) -> None:
        detail = ready.detail
        self._set_test_command(
            detail.identity if can_be_threshold_test(detail.sport) else None, not_a_run_reason()
        )
        self._fill_summary(detail, ready.energy, has_hr_chart=ready.chart is not None)
        self._set_details_visible(True)
        self._zone_model.set_rows(zone_rows(detail))
        self._domain_model.set_rows(domain_rows(detail))
        self._table.setVisible(ready.chart is not None)
        self._domain_table.setVisible(ready.chart is not None)
        self._fit_table_height()
        self._chart.setVisible(ready.chart is not None)
        self._speed_chart.setVisible(ready.speed_chart is not None)
        if ready.chart is None and ready.speed_chart is None:
            self._show_message(
                self.tr("No heart-rate data"),
                self.tr("This activity has no heart-rate samples, so its load is estimated."),
            )
            return
        if ready.chart is not None:
            self._chart.show_prepared(ready.chart)
        if ready.speed_chart is not None:
            self._speed_chart.set_accessible_name(
                self.tr("Pace chart") if detail.sport in PACE_SPORTS else self.tr("Speed chart")
            )
            self._speed_chart.show_prepared(ready.speed_chart)
        self._body.setCurrentWidget(self._charts)

    def _fill_summary(
        self, detail: ActivityDetail, energy: ActivityEnergy | None, *, has_hr_chart: bool
    ) -> None:
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
            "pace": pace_text(average_pace_s_per_km(detail)),
            "ascent": ascent_m_text(detail.summary.ascent_m),
            "calories": active_calories_text(energy),
            "load": QLocale().toString(round(detail.load.trimp_eq)),
            "source": load_source_text(detail.load.source),
            "thresholds": self.tr("{lt1} / {lt2}").format(
                lt1=QLocale().toString(detail.lt1_bpm),
                lt2=QLocale().toString(detail.threshold_bpm),
            ),
            "limits": self.tr("{low} to {high}").format(low=limits.min_bpm, high=limits.max_bpm),
        }
        for key, text in values.items():
            self._summary_values[key].setText(text)
        shown = tuple(
            key
            for key in self._summary_values
            if (key != "pace" or detail.sport in PACE_SPORTS)
            and (key != "calories" or energy is not None)
        )
        self._lay_out_summary(shown)
        self._notice.setText(self._notice_text(detail, has_hr_chart=has_hr_chart))
        self._notice.setVisible(bool(self._notice.text()))

    def _notice_text(self, detail: ActivityDetail, *, has_hr_chart: bool) -> str:
        notes = []
        if not has_hr_chart and detail.speed is not None:
            # the HR chart's own message page is not shown: say it here
            notes.append(
                self.tr("This activity has no heart-rate samples, so its load is estimated.")
            )
        if detail.speed is None:
            notes.append(
                self.tr("This activity has no distance or speed data, so there is no pace chart.")
                if detail.sport in PACE_SPORTS
                else self.tr(
                    "This activity has no distance or speed data, so there is no speed chart."
                )
            )
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

    def _set_test_command(self, identity: ActivityIdentity | None, reason: str) -> None:
        """Use as threshold test works on the activity shown, if it is a run."""
        self._identity = identity
        self._use_as_test.setEnabled(identity is not None)
        self._use_as_test.setToolTip("" if identity is not None else reason)

    def _on_use_as_test(self) -> None:
        if self._identity is not None:
            self.threshold_test_requested.emit(self._identity)

    def _set_details_visible(self, visible: bool) -> None:
        for key, value in self._summary_values.items():
            shown = visible and key in self._summary_shown
            self._summary_captions[key].setVisible(shown)
            value.setVisible(shown)
        self._table.setVisible(visible)
        self._domain_table.setVisible(visible)
        if not visible:
            self._notice.setVisible(False)
            self._title.setText("")

    def _show_message(self, title: str, text: str) -> None:
        self._message.set_content(title, text, "")
        self._body.setCurrentWidget(self._message)
