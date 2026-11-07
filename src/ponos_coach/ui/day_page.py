# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Page of a day: activities, energy, one card per eating moment and the advisories.

Humble view on `day_presentation`: it lays out ready-made texts. The advisories are always on
screen with the guidance, and the fixed notice (general guidance, not medical advice) stays under
the title, outside the scrolling part, whatever the state: calculating, missing data, guidance or
failure. Numbers sit on the right with their unit in the header.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ponos_coach.ui.day_presentation import (
    ACTIVITY_NUMERIC_COLUMNS,
    DayContent,
    DayView,
    MealCardView,
    UnavailableView,
    activity_headers,
    summary_headers,
)
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.text_table_model import TextTableModel
from ponos_coach.ui.tokens import SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_6, TITLE_SCALE

_FRAME_ROWS_EXTRA = 2  # frame of a table without scroll bars, in pixels
_MAX_TEXT_WIDTH = 720
_MEAL_COLUMNS = 4


class DayState(Enum):
    """What the page shows."""

    EMPTY = "empty"
    LOADING = "loading"
    UNAVAILABLE = "unavailable"
    CONTENT = "content"
    FAILED = "failed"


def _label(text: str, parent: QWidget | None = None, *, bold: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if bold:
        font = label.font()
        font.setWeight(font.Weight.DemiBold)
        label.setFont(font)
    return label


def _fit_table(table: QTableView, rows: int) -> None:
    """Size a table to its rows (no scroll bars: the page scrolls)."""
    table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    table.setFixedHeight(
        table.horizontalHeader().sizeHint().height()
        + rows * table.verticalHeader().defaultSectionSize()
        + _FRAME_ROWS_EXTRA * table.frameWidth()
        + _FRAME_ROWS_EXTRA
    )


def _plain_table(
    model: TextTableModel, name: str, parent: QWidget, *, stretch_last: bool
) -> QTableView:
    table = QTableView(parent)
    table.setObjectName(name)
    table.setAccessibleName(name)
    table.setModel(model)
    table.setShowGrid(False)  # no lines
    table.setAlternatingRowColors(True)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setStretchLastSection(stretch_last)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    return table


class DayPage(QWidget):
    """The page of one day, in whichever state the day's calculation is."""

    back_requested = Signal()
    edit_profile_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = DayState.EMPTY
        self._activities_model = TextTableModel(activity_headers(), ACTIVITY_NUMERIC_COLUMNS, self)
        self._totals_model = TextTableModel(summary_headers(), (1,), self)
        self._meal_boxes: list[QGroupBox] = []
        self._advisory_labels: list[QLabel] = []
        self._summary_line = ""
        self._build()

    # --- Construction -------------------------------------------------------------------

    def _build(self) -> None:
        self._back = QPushButton(self.tr("Back to calendar"), self)
        self._back.setObjectName("dayBack")
        self._back.clicked.connect(self.back_requested)
        self._title = QLabel(self)
        self._title.setObjectName("dayTitle")
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        font = self._title.font()
        font.setPointSizeF(font.pointSizeF() * TITLE_SCALE)
        font.setWeight(font.Weight.DemiBold)
        self._title.setFont(font)
        header = QVBoxLayout()
        header.addWidget(self._back, alignment=Qt.AlignmentFlag.AlignLeft)
        header.addWidget(self._title)

        self._notice = _label("", self)
        self._notice.setObjectName("dayNotice")
        self._notice.setMaximumWidth(_MAX_TEXT_WIDTH)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(QWidget(self))  # EMPTY
        self._stack.addWidget(self._build_loading())
        self._stack.addWidget(self._build_unavailable())
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("dayScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._stack.addWidget(self._scroll)
        self._stack.addWidget(self._build_failed())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_4, SPACE_4, SPACE_4, SPACE_4)
        layout.setSpacing(SPACE_3)
        layout.addLayout(header)
        layout.addWidget(self._notice)
        layout.addWidget(self._stack, stretch=1)

    def _build_loading(self) -> QWidget:
        page = QWidget(self)
        column = QVBoxLayout(page)
        column.addWidget(_label(self.tr("Calculating the day..."), page))
        bar = QProgressBar(page)
        bar.setRange(0, 0)  # the length is not known
        bar.setTextVisible(False)
        bar.setMaximumWidth(_MAX_TEXT_WIDTH)
        column.addWidget(bar)
        column.addStretch(1)
        return page

    def _build_unavailable(self) -> QWidget:
        page = QWidget(self)
        column = QVBoxLayout(page)
        column.setSpacing(SPACE_2)
        self._missing_title = _label("", page, bold=True)
        self._missing_title.setObjectName("dayMissingTitle")
        self._missing_what = _label("", page)
        self._missing_what.setObjectName("dayMissingWhat")
        self._missing_remedy = _label("", page)
        self._missing_remedy.setObjectName("dayMissingRemedy")
        self._edit_profile = QPushButton(self.tr("Edit profile..."), page)
        self._edit_profile.setObjectName("dayEditProfile")
        self._edit_profile.clicked.connect(self.edit_profile_requested)
        for widget in (self._missing_title, self._missing_what, self._missing_remedy):
            widget.setMaximumWidth(_MAX_TEXT_WIDTH)
            column.addWidget(widget)
        column.addSpacing(SPACE_2)
        column.addWidget(self._edit_profile, alignment=Qt.AlignmentFlag.AlignLeft)
        column.addStretch(1)
        return page

    def _build_failed(self) -> QWidget:
        page = QWidget(self)
        column = QVBoxLayout(page)
        self._failed_title = _label("", page, bold=True)
        self._failed_title.setObjectName("dayFailedTitle")
        self._failed_detail = _label("", page)
        self._failed_detail.setObjectName("dayFailedDetail")
        column.addWidget(self._failed_title)
        column.addWidget(self._failed_detail)
        column.addStretch(1)
        return page

    # --- Test accessors --------------------------------------------------------------------

    @property
    def state(self) -> DayState:
        """What the page shows now."""
        return self._state

    @property
    def title_text(self) -> str:
        """The day's title."""
        return self._title.text()

    @property
    def notice_text(self) -> str:
        """The fixed notice (general guidance, not medical advice)."""
        return self._notice.text()

    @property
    def back_button(self) -> QPushButton:
        """The "Back to calendar" button."""
        return self._back

    @property
    def edit_profile_button(self) -> QPushButton:
        """The "Edit profile..." button of the missing-data state."""
        return self._edit_profile

    @property
    def missing_texts(self) -> tuple[str, str, str]:
        """Title, what is missing and what to do, in the missing-data state."""
        return (
            self._missing_title.text(),
            self._missing_what.text(),
            self._missing_remedy.text(),
        )

    @property
    def failure_texts(self) -> tuple[str, str]:
        """Title and detail of the failure state."""
        return (self._failed_title.text(), self._failed_detail.text())

    @property
    def summary_line(self) -> str:
        """The line with the day's goal and training level."""
        return self._summary_line

    @property
    def activities_model(self) -> TextTableModel:
        """The model of the activities table."""
        return self._activities_model

    @property
    def totals_model(self) -> TextTableModel:
        """The model of the totals table."""
        return self._totals_model

    @property
    def meal_titles(self) -> tuple[str, ...]:
        """The titles of the meal cards, in display order."""
        return tuple(box.title() for box in self._meal_boxes)

    @property
    def advisory_texts(self) -> tuple[str, ...]:
        """The advisories shown (and the unreadable-files note), in order."""
        return tuple(label.text() for label in self._advisory_labels)

    def meal_texts(self, index: int) -> tuple[str, ...]:
        """All the texts inside a meal card."""
        return tuple(label.text() for label in self._meal_boxes[index].findChildren(QLabel))

    def advisories_visible(self) -> bool:
        """True if every advisory is on screen (not hidden by the page)."""
        return all(not label.isHidden() for label in self._advisory_labels)

    # --- States ------------------------------------------------------------------------------

    def show_loading(self, title: str, notice: str) -> None:
        """Show the page while the day is being calculated."""
        self._title.setText(title)
        self._notice.setText(notice)
        self._set_state(DayState.LOADING)

    def show_content(self, content: DayContent) -> None:
        """Show the guidance for the day, or what is missing."""
        self._title.setText(content.title)
        self._notice.setText(content.notice)
        if isinstance(content, UnavailableView):
            self._missing_title.setText(content.missing.title)
            self._missing_what.setText(content.missing.what)
            self._missing_remedy.setText(content.missing.remedy)
            self._set_state(DayState.UNAVAILABLE)
            return
        self._scroll.setWidget(self._build_content(content))
        self._set_state(DayState.CONTENT)

    def show_failure(self, message: ErrorMessage) -> None:
        """Show why the day could not be calculated, and what to try."""
        self._failed_title.setText(message.title)
        self._failed_detail.setText(f"{message.what} {message.remedy}")
        self._set_state(DayState.FAILED)

    def clear(self) -> None:
        """Forget what the page shows (profile changed or closed)."""
        self._title.clear()
        self._notice.clear()
        self._meal_boxes = []
        self._advisory_labels = []
        self._scroll.takeWidget()
        self._set_state(DayState.EMPTY)

    def _set_state(self, state: DayState) -> None:
        self._state = state
        self._stack.setCurrentIndex(list(DayState).index(state))

    # --- Guidance content -----------------------------------------------------------------

    def _build_content(self, view: DayView) -> QWidget:
        root = QWidget()
        column = QVBoxLayout(root)
        column.setContentsMargins(0, 0, SPACE_4, 0)
        column.setSpacing(SPACE_4)
        self._meal_boxes = []
        self._advisory_labels = []
        notes = [*view.advisories, *([view.unreadable_note] if view.unreadable_note else [])]
        if notes:
            column.addWidget(self._build_advisories(notes, root))
        column.addWidget(self._build_summary(view, root))
        column.addWidget(self._build_activities(view, root))
        column.addWidget(_label(self.tr("Meals and training intakes"), root, bold=True))
        for card in view.meals:
            column.addWidget(self._build_meal(card, root))
        column.addStretch(1)
        return root

    def _build_advisories(self, notes: list[str], parent: QWidget) -> QGroupBox:
        box = QGroupBox(self.tr("Things to know"), parent)
        box.setObjectName("dayAdvisories")
        inner = QVBoxLayout(box)
        for note in notes:
            label = _label(note, box)
            label.setMaximumWidth(_MAX_TEXT_WIDTH)
            inner.addWidget(label)
            self._advisory_labels.append(label)
        return box

    def _build_summary(self, view: DayView, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(SPACE_2)
        # {goal}: the nutrition goal of the day; {level}: how demanding its training is.
        self._summary_line = self.tr("Goal: {goal}. Day: {level}.").format(
            goal=view.goal, level=view.level
        )
        inner.addWidget(_label(self._summary_line, box))
        self._totals_model.set_rows(view.totals)
        table = _plain_table(
            self._totals_model, self.tr("Energy and macros"), box, stretch_last=False
        )
        _fit_table(table, len(view.totals))
        table.setMaximumWidth(_MAX_TEXT_WIDTH // 2)
        inner.addWidget(table)
        return box

    def _build_activities(self, view: DayView, parent: QWidget) -> QWidget:
        box = QWidget(parent)
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(SPACE_2)
        inner.addWidget(_label(self.tr("Activities"), box, bold=True))
        if not view.activities:
            inner.addWidget(_label(self.tr("No activities on this day."), box))
            self._activities_model.set_rows(())
            return box
        self._activities_model.set_rows(view.activities)
        table = _plain_table(
            self._activities_model, self.tr("Day activities"), box, stretch_last=True
        )
        _fit_table(table, len(view.activities))
        inner.addWidget(table)
        return box

    def _build_meal(self, card: MealCardView, parent: QWidget) -> QGroupBox:
        # {meal}: the name of the eating moment; {time}: its time of day.
        box = QGroupBox(self.tr("{meal} at {time}").format(meal=card.title, time=card.time), parent)
        inner = QVBoxLayout(box)
        inner.setSpacing(SPACE_1 + SPACE_2)
        if card.moved_note:
            inner.addWidget(_label(card.moved_note, box))
        grid = QGridLayout()
        grid.setHorizontalSpacing(SPACE_6)
        headers = (
            self.tr("Energy (kcal)"),
            self.tr("Carbohydrates (g)"),
            self.tr("Protein (g)"),
            self.tr("Fat (g)"),
        )
        values = (card.kcal, card.carbs, card.protein, card.fat)
        for column, (header, value) in enumerate(zip(headers, values, strict=True)):
            for row, text in enumerate((header, value)):
                label = QLabel(text, box)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                grid.addWidget(label, row, column)
        grid.setColumnStretch(_MEAL_COLUMNS, 1)
        inner.addLayout(grid)
        if card.hourly:
            inner.addWidget(_label(card.hourly, box))
        inner.addWidget(_label(self._examples_title(card), box, bold=True))
        for example in card.examples:
            inner.addWidget(_label(example, box))
        self._meal_boxes.append(box)
        return box

    def _examples_title(self, card: MealCardView) -> str:
        if card.examples:
            return self.tr("Examples")
        return self.tr("No food example matches these amounts.")
