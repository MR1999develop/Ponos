# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-FILT-03 and T-FILT-04 (state and bar): reading the fields, the shared state and the bar."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QPushButton, QToolButton, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import ActivityFilter, NumericRange, Sport
from ponos_coach.ui.activity_filters import (
    FieldProblem,
    FilterField,
    FilterTexts,
    SharedFilterState,
    canonical_texts,
    parse_date_text,
    parse_filter,
    problem_text,
)
from ponos_coach.ui.filter_bar import FilterBar
from ponos_coach.ui.tokens import Theme


def settings_in(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "filters.ini"), QSettings.Format.IniFormat)


def test_empty_fields_are_no_filter_at_all() -> None:
    outcome = parse_filter(FilterTexts())

    assert outcome.criteria == ActivityFilter()
    assert outcome.criteria is not None
    assert outcome.criteria.is_empty
    assert outcome.problems == {}


def test_fields_are_read_in_the_units_the_bar_shows() -> None:
    outcome = parse_filter(
        FilterTexts(
            date_from="2026-03-01",
            date_to="2026-03-31",
            duration_min="1:30",
            duration_max="2:00:00",
            distance_min="10,5",
            distance_max="21.1",
            ascent_min="100",
            ascent_max="900",
            sports=frozenset({Sport.RUNNING, Sport.TRAIL}),
        )
    )

    assert outcome.criteria == ActivityFilter(
        date_from=date(2026, 3, 1),
        date_to=date(2026, 3, 31),
        duration_s=NumericRange(5400.0, 7200.0),
        distance_m=NumericRange(10_500.0, 21_100.0),
        ascent_m=NumericRange(100.0, 900.0),
        sports=frozenset({Sport.RUNNING, Sport.TRAIL}),
    )


@pytest.mark.parametrize(("text", "seconds"), [("90", 5400.0), ("90,5", 5430.0), ("1:30", 5400.0)])
def test_a_duration_is_minutes_or_hours_and_minutes(text: str, seconds: float) -> None:
    outcome = parse_filter(FilterTexts(duration_min=text))

    assert outcome.criteria is not None
    assert outcome.criteria.duration_s.minimum == seconds


def test_a_minimum_above_the_maximum_is_swapped_not_refused() -> None:
    outcome = parse_filter(
        FilterTexts(
            distance_min="20", distance_max="5", date_from="2026-06-01", date_to="2026-01-01"
        )
    )

    assert outcome.criteria is not None
    assert outcome.criteria.distance_m == NumericRange(5000.0, 20_000.0)
    assert (outcome.criteria.date_from, outcome.criteria.date_to) == (
        date(2026, 1, 1),
        date(2026, 6, 1),
    )


@pytest.mark.parametrize(
    ("field", "text", "problem"),
    [
        (FilterField.DISTANCE_MIN, "-3", FieldProblem.NEGATIVE),
        (FilterField.DURATION_MAX, chr(0x2212) + "5", FieldProblem.NEGATIVE),
        (FilterField.ASCENT_MIN, "abc", FieldProblem.NOT_A_NUMBER),
        (FilterField.DISTANCE_MAX, "1e3", FieldProblem.NOT_A_NUMBER),
        (FilterField.DURATION_MIN, "1:75", FieldProblem.NOT_A_NUMBER),
        (FilterField.ASCENT_MAX, "9" * 40, FieldProblem.TOO_LARGE),
        (FilterField.DATE_FROM, "2026-02-31", FieldProblem.NOT_A_DATE),
        (FilterField.DATE_TO, "tomorrow", FieldProblem.NOT_A_DATE),
        (FilterField.DATE_TO, "9" * 80, FieldProblem.NOT_A_DATE),
    ],
)
def test_a_wrong_field_is_named_and_no_partial_filter_is_made(
    field: FilterField, text: str, problem: FieldProblem
) -> None:
    outcome = parse_filter(FilterTexts().with_text(field, text))

    assert outcome.criteria is None
    assert outcome.problems[field] is problem


def test_every_wrong_field_is_reported_not_only_the_first() -> None:
    outcome = parse_filter(FilterTexts(date_from="x", distance_min="y", ascent_max="-1"))

    assert set(outcome.problems) == {
        FilterField.DATE_FROM,
        FilterField.DISTANCE_MIN,
        FilterField.ASCENT_MAX,
    }


def test_the_message_says_what_to_write_and_does_not_repeat_the_text() -> None:
    message = problem_text(FilterField.DISTANCE_MIN, FieldProblem.NOT_A_NUMBER)

    assert "Minimum distance (km)" in message
    assert "10.5" in message  # an example of what is valid
    assert "abc" not in message


@pytest.mark.parametrize("text", ["2026-3-5", "2026-03-05", " 2026-03-05 "])
def test_iso_dates_are_read_with_or_without_leading_zeros(text: str) -> None:
    assert parse_date_text(text) == date(2026, 3, 5)


def test_the_criteria_round_trip_through_the_canonical_texts() -> None:
    criteria = ActivityFilter(
        date_from=date(2026, 3, 1),
        duration_s=NumericRange(None, 5400.0),
        distance_m=NumericRange(10_500.0, None),
        ascent_m=NumericRange(0.0, 900.0),
        sports=frozenset({Sport.CYCLING}),
    )

    assert parse_filter(canonical_texts(criteria)).criteria == criteria


# --- The shared state (T-FILT-04) -----------------------------------------------------------


def test_the_state_tells_everyone_when_the_filter_changes_and_not_when_it_does_not(
    tmp_path: Path,
) -> None:
    state = SharedFilterState(settings_in(tmp_path))
    changes: list[object] = []
    state.changed.connect(changes.append)

    state.apply(FilterTexts(distance_min="10"))
    state.apply(FilterTexts(distance_min="10,0"))  # the same filter written differently
    state.apply(FilterTexts(distance_min="abc"))  # wrong: the filter in force stays

    assert len(changes) == 1
    assert state.criteria.distance_m == NumericRange(10_000.0, None)


def test_the_last_filter_is_remembered_between_runs(tmp_path: Path) -> None:
    first = SharedFilterState(settings_in(tmp_path))
    first.apply(
        FilterTexts(date_from="2026-03-01", ascent_min="250", sports=frozenset({Sport.HIKING}))
    )

    second = SharedFilterState(settings_in(tmp_path))

    assert second.criteria == first.criteria
    assert second.criteria.sports == frozenset({Sport.HIKING})


def test_damaged_saved_values_mean_no_filter_not_an_error(tmp_path: Path) -> None:
    settings = settings_in(tmp_path)
    settings.setValue("filters/dateFrom", "not a date")
    settings.setValue("filters/distanceMin", "-4")
    settings.setValue("filters/sports", "running,teleport")

    state = SharedFilterState(settings)

    assert state.criteria == ActivityFilter()  # one bad field makes the whole saved filter void


def test_clearing_the_filter_forgets_it(tmp_path: Path) -> None:
    state = SharedFilterState(settings_in(tmp_path))
    state.apply(FilterTexts(distance_min="10"))

    state.clear()

    assert state.criteria.is_empty
    assert SharedFilterState(settings_in(tmp_path)).criteria.is_empty


# --- The bar --------------------------------------------------------------------------------


def make_bar(qtbot: QtBot, tmp_path: Path) -> tuple[FilterBar, SharedFilterState]:
    state = SharedFilterState(settings_in(tmp_path))
    bar = FilterBar(state, lambda: Theme.LIGHT)
    qtbot.addWidget(bar)
    bar.show()
    return bar, state


def test_typing_in_a_field_and_leaving_it_applies_the_filter(qtbot: QtBot, tmp_path: Path) -> None:
    bar, state = make_bar(qtbot, tmp_path)

    bar.edits[FilterField.DISTANCE_MIN].setText("10,5")
    bar.edits[FilterField.DISTANCE_MIN].editingFinished.emit()

    assert state.criteria.distance_m == NumericRange(10_500.0, None)
    assert not bar.problems_label.isVisible()


def test_a_wrong_field_is_marked_and_the_bar_says_what_to_write(
    qtbot: QtBot, tmp_path: Path
) -> None:
    bar, state = make_bar(qtbot, tmp_path)

    bar.edits[FilterField.ASCENT_MAX].setText("lots")
    bar.edits[FilterField.ASCENT_MAX].editingFinished.emit()

    assert state.criteria.is_empty  # nothing changed
    assert bar.problems_label.isVisible()
    assert "Maximum ascent (m)" in bar.problems_label.text()
    assert bar.edits[FilterField.ASCENT_MAX].toolTip() == bar.problems_label.text()
    assert (
        "border" in bar.edits[FilterField.ASCENT_MAX].styleSheet()
    )  # not only words: a border too
    assert bar.edits[FilterField.ASCENT_MAX].accessibleDescription()


def test_choosing_sports_filters_by_them_and_the_button_says_which(
    qtbot: QtBot, tmp_path: Path
) -> None:
    bar, state = make_bar(qtbot, tmp_path)
    button = bar.findChild(QToolButton, "filterSports")
    assert button is not None
    assert button.text() == "All sports"

    bar.sport_actions[Sport.RUNNING].setChecked(True)
    assert button.text() == "Running"
    bar.sport_actions[Sport.CYCLING].setChecked(True)

    assert state.criteria.sports == frozenset({Sport.RUNNING, Sport.CYCLING})
    assert button.text() == "2 sports"


def test_the_bar_follows_the_state_when_it_changes_from_elsewhere(
    qtbot: QtBot, tmp_path: Path
) -> None:
    bar, state = make_bar(qtbot, tmp_path)

    state.apply(FilterTexts(distance_max="21.1", sports=frozenset({Sport.TRAIL})))

    assert bar.edits[FilterField.DISTANCE_MAX].text() in {"21.1", "21,1"}
    assert bar.sport_actions[Sport.TRAIL].isChecked()
    bar.clear_filters()
    assert bar.edits[FilterField.DISTANCE_MAX].text() == ""
    assert not bar.sport_actions[Sport.TRAIL].isChecked()


def test_every_field_has_a_name_for_the_screen_reader(qtbot: QtBot, tmp_path: Path) -> None:
    bar, _state = make_bar(qtbot, tmp_path)

    names = [edit.accessibleName() for edit in bar.edits.values()]

    assert all(names)
    assert len(set(names)) == len(names)  # no two fields sound the same


def test_the_clear_button_lines_up_with_the_controls_not_with_their_titles(
    qtbot: QtBot, tmp_path: Path
) -> None:
    bar, _state = make_bar(qtbot, tmp_path)
    bar.show()
    qtbot.waitExposed(bar)
    bar.resize(1400, 200)  # a wide Activities pane: every group and the button on one row
    qtbot.wait(20)
    clear = next(b for b in bar.findChildren(QPushButton) if b.text() == "Clear filters")
    sports = bar.findChild(QToolButton, "filterSports")
    assert isinstance(sports, QToolButton)

    def bottom(widget: QWidget) -> int:
        return widget.mapTo(bar, widget.rect().bottomLeft()).y()

    # Beside the sports, below the room of a title: in line with the controls of its row.
    assert abs(bottom(clear) - bottom(sports)) <= 3
    assert all(edit.width() <= 160 for edit in bar.edits.values())  # as wide as a date
