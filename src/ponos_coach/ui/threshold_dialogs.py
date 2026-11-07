# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Threshold test windows: the instructions, the review of a test and the history.

Humble views: the texts and numbers arrive ready from `threshold_presentation`; here there are only
widgets. Everything is plain text and reachable from the keyboard.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from PySide6.QtCore import QLocale, QSortFilterProxyModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.assessment.domain.defaults import (
    RETEST_MAX_DAYS,
    RETEST_MIN_DAYS,
    TEST_DURATION_S,
    THRESHOLD_WINDOW_S,
)
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.state_page import StatePage
from ponos_coach.ui.text_table_model import TextTableModel
from ponos_coach.ui.threshold_history_model import ThresholdHistoryModel, sorted_history_model
from ponos_coach.ui.threshold_presentation import (
    ReviewView,
    ThresholdHistory,
    retest_text,
)
from ponos_coach.ui.tokens import DIALOG_MIN_WIDTH, SPACE_2, SPACE_4

_SECONDS_PER_MINUTE = 60
_DAYS_PER_WEEK = 7
_TABLE_MIN_HEIGHT = 140
_ZONE_TABLE_HEIGHT = 176
_HISTORY_MIN_WIDTH = 720


def _label(text: str, parent: QWidget, name: str = "") -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


def _table(parent: QWidget, name: str, accessible_name: str) -> QTableView:
    table = QTableView(parent)
    table.setObjectName(name)
    table.setAccessibleName(accessible_name)
    table.setShowGrid(False)  # no vertical or horizontal lines
    table.setAlternatingRowColors(True)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


class ThresholdInstructionsDialog(QDialog):
    """How to do the 30-minute test."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Test instructions"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        locale = QLocale()
        minutes = locale.toString(TEST_DURATION_S // _SECONDS_PER_MINUTE)
        last = locale.toString(THRESHOLD_WINDOW_S // _SECONDS_PER_MINUTE)
        first_week = locale.toString(RETEST_MIN_DAYS // _DAYS_PER_WEEK)
        last_week = locale.toString(RETEST_MAX_DAYS // _DAYS_PER_WEEK)
        steps = (
            self.tr("Warm up for 10 to 15 minutes, running easily."),
            self.tr(
                "Run for {minutes} minutes at the hardest effort you can keep steady for the "
                "whole time. Do not start too fast."
            ).format(minutes=minutes),
            self.tr("Do it alone and on flat ground, so that nothing changes your effort."),
            self.tr("Finish with an easy cool-down."),
        )
        title = _label(self.tr("How to do the threshold test"), self, "instructionsTitle")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        intro = _label(
            self.tr(
                "The {minutes}-minute test measures your threshold heart rate and pace. Record "
                "it with your watch."
            ).format(minutes=minutes),
            self,
            "instructionsIntro",
        )
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(title)
        outer.addWidget(intro)
        for number, text in enumerate(steps, start=1):
            outer.addWidget(
                _label(f"{locale.toString(number)}. {text}", self, f"instructionsStep{number}")
            )
        outer.addWidget(
            _label(
                self.tr(
                    "Your threshold heart rate is the average of the last {minutes} minutes of the "
                    "test."
                ).format(minutes=last),
                self,
                "instructionsAverage",
            )
        )
        outer.addWidget(
            _label(
                self.tr(
                    "Repeat the test every {first} to {last} weeks, to keep your zones up to date."
                ).format(first=first_week, last=last_week),
                self,
                "instructionsRepeat",
            )
        )
        outer.addWidget(
            _label(
                self.tr(
                    "A test is not a diagnosis. If you have symptoms or any doubt about your "
                    "health, talk to a doctor before you do it."
                ),
                self,
                "instructionsHealth",
            )
        )
        outer.addWidget(
            _label(
                self.tr(
                    "Afterwards, choose the activity in Activities and use Threshold test > "
                    "Use as threshold test."
                ),
                self,
                "instructionsNext",
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)


class ThresholdResultDialog(QDialog):
    """The review of an activity as the threshold test: loading, a result to apply, or why not."""

    apply_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Threshold test"))
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self._build()
        self.show_loading()

    def _build(self) -> None:
        self._loading = QWidget(self)
        self._loading_label = _label(self.tr("Evaluating the activity..."), self._loading)
        bar = QProgressBar(self._loading)
        bar.setRange(0, 0)  # the length of the evaluation is not known
        bar.setTextVisible(False)
        bar.setAccessibleName(self.tr("Evaluating the activity"))
        column = QVBoxLayout(self._loading)
        column.setSpacing(SPACE_2)
        column.addWidget(self._loading_label)
        column.addWidget(bar)
        column.addStretch(1)

        self._message = StatePage(self)
        self._message.button.setVisible(False)

        self._review = QWidget(self)
        self._values: dict[str, QLabel] = {}
        form = QFormLayout()
        form.setVerticalSpacing(SPACE_2)
        rows = (
            ("day", self.tr("Test day")),
            ("start", self.tr("Segment starts at")),
            ("selection", self.tr("Segment chosen as")),
            ("threshold", self.tr("Threshold heart rate (bpm)")),
            ("pace", self.tr("Threshold pace (min:s per km)")),
            ("current", self.tr("Your current threshold (bpm)")),
        )
        for key, text in rows:
            value = _label("", self._review, f"threshold-{key}")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._values[key] = value
            form.addRow(text, value)
        self._zone_model = TextTableModel((), (), self)
        self._zones = _table(self._review, "thresholdZones", self.tr("Zones of the test"))
        self._zones.setMaximumHeight(_ZONE_TABLE_HEIGHT)
        self._failure = _label("", self._review, "thresholdFailure")
        self._failure.setAccessibleName(self.tr("Problem"))
        self._failure.hide()
        review = QVBoxLayout(self._review)
        review.setContentsMargins(0, 0, 0, 0)
        review.setSpacing(SPACE_4)
        review.addLayout(form)
        review.addWidget(self._zones)
        review.addWidget(self._failure)

        self._pages = QStackedWidget(self)
        for page in (self._loading, self._message, self._review):
            self._pages.addWidget(page)

        self._buttons = QDialogButtonBox(self)
        self._apply = QPushButton(self.tr("Apply threshold"), self)
        self._apply.setObjectName("thresholdApply")
        self._apply.setDefault(True)
        self._apply.clicked.connect(self._on_apply_clicked)
        self._buttons.addButton(self._apply, QDialogButtonBox.ButtonRole.AcceptRole)
        self._close = self._buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self._buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._pages, stretch=1)
        outer.addWidget(self._buttons)

    # --- Test accessors ---------------------------------------------------------------------

    @property
    def apply_button(self) -> QPushButton:
        """The "Apply threshold" button."""
        return self._apply

    @property
    def close_button(self) -> QPushButton:
        """The "Close" button."""
        return self._close

    @property
    def zone_model(self) -> TextTableModel:
        """The zones of the test."""
        return self._zone_model

    @property
    def message_page(self) -> StatePage:
        """The page with a rejection or an error."""
        return self._message

    def value_text(self, key: str) -> str:
        """The text of one row of the review."""
        return self._values[key].text()

    @property
    def failure_text(self) -> str:
        """The problem shown under the review (empty if there is none)."""
        return "" if self._failure.isHidden() else self._failure.text()

    @property
    def is_loading(self) -> bool:
        """True while the activity is being evaluated."""
        return self._pages.currentWidget() is self._loading

    @property
    def is_reviewing(self) -> bool:
        """True while a result is shown, ready to apply."""
        return self._pages.currentWidget() is self._review

    # --- States -----------------------------------------------------------------------------

    def show_loading(self) -> None:
        """While the data thread evaluates the activity."""
        self._pages.setCurrentWidget(self._loading)
        self._apply.setEnabled(False)
        self._apply.setToolTip(self.tr("The activity is still being evaluated."))

    def show_message(self, message: ErrorMessage) -> None:
        """A rejection or an error: what happened and what to do."""
        self._message.set_content(message.title, f"{message.what}\n\n{message.remedy}", "")
        self._pages.setCurrentWidget(self._message)
        self._apply.setEnabled(False)
        self._apply.setToolTip(self.tr("This activity cannot be applied as a threshold test."))
        self._close.setFocus()

    def show_review(self, view: ReviewView) -> None:
        """A test that can be applied."""
        texts = {
            "day": view.day,
            "start": view.segment_start,
            "selection": view.selection,
            "threshold": view.threshold,
            "pace": view.pace,
            "current": view.current,
        }
        for key, text in texts.items():
            self._values[key].setText(text)
        numeric = range(1, len(view.zone_headers))
        previous = self._zone_model
        self._zone_model = TextTableModel(view.zone_headers, numeric, self)
        self._zone_model.set_rows(view.zone_rows)
        self._zones.setModel(self._zone_model)
        previous.deleteLater()
        self._failure.hide()
        self._pages.setCurrentWidget(self._review)
        self.set_busy(False)
        self._apply.setFocus()

    def set_busy(self, busy: bool) -> None:
        """While the test is being applied the button waits."""
        self._apply.setEnabled(not busy and self.is_reviewing)
        self._apply.setToolTip(self.tr("The threshold is being saved...") if busy else "")

    def show_failure(self, message: ErrorMessage) -> None:
        """The apply did not go through: the review stays, with the problem under it."""
        self._failure.setText(f"{message.title}. {message.what} {message.remedy}")
        self._failure.show()
        self.set_busy(False)

    def _on_apply_clicked(self) -> None:
        self.set_busy(True)
        self.apply_requested.emit()


class ThresholdHistoryDialog(QDialog):
    """The tests applied so far, and when to repeat the test."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(self.tr("Threshold tests"))
        self.setMinimumWidth(_HISTORY_MIN_WIDTH)
        self._model = ThresholdHistoryModel(self)
        self._proxy = sorted_history_model(self._model, self)
        self._table = _table(self, "thresholdHistory", self.tr("Threshold tests"))
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setMinimumHeight(_TABLE_MIN_HEIGHT)
        self._empty = StatePage(self)
        self._empty.button.setVisible(False)
        self._empty.set_content(
            self.tr("No threshold tests yet"),
            self.tr(
                "To add one, do the test, then choose the activity and use Threshold test > "
                "Use as threshold test."
            ),
            "",
        )
        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._empty)
        self._stack.addWidget(self._table)
        self._retest = _label("", self, "thresholdRetest")
        self._retest.setAccessibleName(self.tr("When to repeat the test"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.reject)
        outer = QVBoxLayout(self)
        outer.setSpacing(SPACE_4)
        outer.addWidget(self._stack, stretch=1)
        outer.addWidget(self._retest)
        outer.addWidget(buttons)
        self._stack.setCurrentWidget(self._empty)

    @property
    def model(self) -> ThresholdHistoryModel:
        """The table model."""
        return self._model

    @property
    def proxy(self) -> QSortFilterProxyModel:
        """The sorting proxy."""
        return self._proxy

    @property
    def table(self) -> QTableView:
        """The history table."""
        return self._table

    @property
    def empty_page(self) -> StatePage:
        """The empty state."""
        return self._empty

    @property
    def retest_label_text(self) -> str:
        """The retest sentence."""
        return self._retest.text()

    def show_history(self, history: ThresholdHistory, zone: ZoneInfo) -> None:
        """Show the tests (or the empty state) and the retest status."""
        self._model.set_entries(history.entries, zone)
        self._stack.setCurrentWidget(self._table if history.entries else self._empty)
        self._retest.setText(retest_text(history.retest))
