# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cycling and other-activities threshold HR in "Edit profile": form model, history columns and
dialog. All the values are made up (no real person)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QLocale, QSettings, Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.measurements import (
    MeasurementHistory,
    MeasurementProblem,
    ThresholdReset,
)
from ponos_coach.ui.heart_rate_rules import HeartRateProblem
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.measurement_history_model import Column, MeasurementHistoryModel
from ponos_coach.ui.measurements_form import (
    EditField,
    EditIssue,
    FieldChange,
    MeasurementsForm,
    MeasurementsRequest,
)
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.profile_edit_dialog import ProfileEditDialog
from ponos_coach.ui.tokens import Theme
from tests.ui.test_profile_data_window import (
    PROFILE,
    click,
    first_load,
    open_edit_dialog,
    window_with_activities,
)
from tests.ui.workspace_support import WAIT_MS, make_settings, wait_profile_saved

TODAY = date(2026, 6, 30)
FIRST = date(2026, 1, 1)
LATER = date(2026, 3, 1)


@pytest.fixture(autouse=True)
def english_numbers() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def athlete(bike: int | None = None, other: int | None = None, threshold: int = 170) -> Physiology:
    return Physiology(
        HeartRateLimits(50, 190),
        ThresholdHeartRate(threshold),
        Sex.MALE,
        None if bike is None else ThresholdHeartRate(bike),
        None if other is None else ThresholdHeartRate(other),
    )


def history_of(*entries: PhysiologyEntry) -> MeasurementHistory:
    return MeasurementHistory(None, (), entries)


def default_history() -> MeasurementHistory:
    return history_of(PhysiologyEntry(FIRST, athlete()))


def form_with_bike_in_force() -> MeasurementsForm:
    return MeasurementsForm(
        history_of(PhysiologyEntry(FIRST, athlete()), PhysiologyEntry(LATER, athlete(bike=160))),
        TODAY,
        PROFILE,
    )


def request_of(form: MeasurementsForm) -> MeasurementsRequest:
    result = form.build()
    assert isinstance(result, MeasurementsRequest), result
    return result


# --- Form model -----------------------------------------------------------------------------


def test_unset_thresholds_are_empty_and_show_their_defaults() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    assert form.threshold_text(EditField.BIKE_THRESHOLD) == ""
    assert form.threshold_text(EditField.OTHER_THRESHOLD) == ""
    assert form.threshold_default(EditField.BIKE_THRESHOLD) == 166
    assert form.threshold_default(EditField.OTHER_THRESHOLD) == 170
    assert not form.has_changes


def test_the_defaults_follow_the_running_threshold_being_typed() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_threshold_bpm(160)

    assert form.threshold_default(EditField.BIKE_THRESHOLD) == 156
    assert form.threshold_default(EditField.OTHER_THRESHOLD) == 160


def test_a_typed_cycling_threshold_is_saved_as_a_threshold_and_others_stay_untouched() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_bike_threshold("158")

    update = request_of(form).update
    assert update is not None
    assert update.bike_threshold == ThresholdHeartRate(158)
    assert update.other_threshold is None
    assert update.threshold_bpm is None


def test_the_change_says_the_value_that_applied_that_day() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_bike_threshold("158")

    assert FieldChange(EditField.BIKE_THRESHOLD, 166) in form.changes()  # the default applied


def test_a_threshold_equal_to_the_one_in_force_is_not_a_change() -> None:
    form = form_with_bike_in_force()

    form.edit_bike_threshold("160")

    assert not form.has_changes
    assert form.build() == (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)


def test_emptying_a_set_threshold_goes_back_to_the_default() -> None:
    form = form_with_bike_in_force()

    form.edit_bike_threshold("")

    update = request_of(form).update
    assert update is not None
    assert update.bike_threshold is ThresholdReset.DEFAULT


def test_an_empty_field_of_an_unset_threshold_is_not_a_change() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_bike_threshold("")

    assert not form.has_changes


def test_untouched_optional_fields_follow_the_chosen_day() -> None:
    form = form_with_bike_in_force()

    assert form.threshold_text(EditField.BIKE_THRESHOLD) == "160"
    form.set_valid_from(date(2026, 2, 1))
    assert form.threshold_text(EditField.BIKE_THRESHOLD) == ""


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("39", HeartRateProblem.NOT_A_THRESHOLD),
        ("251", HeartRateProblem.NOT_A_THRESHOLD),
        ("abc", HeartRateProblem.NOT_A_THRESHOLD),
        ("50", HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN),
        ("191", HeartRateProblem.THRESHOLD_ABOVE_MAX),
    ],
)
def test_an_invalid_cycling_threshold_is_reported_on_its_field(
    text: str, problem: HeartRateProblem
) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_bike_threshold(text)

    assert form.issues() == (EditIssue(EditField.BIKE_THRESHOLD, problem),)
    assert form.has_changes  # a value to correct enables Save, which then shows the problem


@pytest.mark.parametrize("text", ["51", "190"])
def test_the_limit_values_of_an_optional_threshold_are_accepted(text: str) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold(text)

    assert form.issues() == ()


def test_lowering_the_maximum_below_an_entered_threshold_is_reported_on_that_field() -> None:
    form = form_with_bike_in_force()

    form.edit_max_bpm(155)

    assert EditIssue(EditField.BIKE_THRESHOLD, HeartRateProblem.THRESHOLD_ABOVE_MAX) in (
        form.issues()
    )


def test_a_sport_threshold_alone_makes_a_heart_rate_period() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold("140")

    [pending] = form.pending_periods()
    assert pending.period.valid_from == TODAY


# --- History table -------------------------------------------------------------------------


def test_the_history_has_a_column_per_threshold_and_empty_cells_for_the_defaults(
    qtbot: QtBot,
) -> None:
    model = MeasurementHistoryModel()
    model.set_history(
        history_of(PhysiologyEntry(FIRST, athlete()), PhysiologyEntry(LATER, athlete(160, 140)))
    )

    cells = [
        [model.data(model.index(row, column)) for column in (Column.THRESHOLD, 5, 6)]
        for row in range(model.rowCount())
    ]
    headers = [
        model.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole)
        for c in (Column.THRESHOLD, Column.BIKE_THRESHOLD, Column.OTHER_THRESHOLD)
    ]

    assert cells == [["170", "", ""], ["170", "160", "140"]]
    assert headers == [
        "Running threshold HR (bpm)",
        "Cycling threshold HR (bpm)",
        "Other activities threshold HR (bpm)",
    ]


# --- Dialog -----------------------------------------------------------------------------------


def dialog_of(qtbot: QtBot, history: MeasurementHistory) -> ProfileEditDialog:
    dialog = ProfileEditDialog(MeasurementsForm(history, TODAY, PROFILE), history, Theme.LIGHT)
    qtbot.addWidget(dialog)
    return dialog


def test_the_empty_fields_show_the_defaults_as_placeholders(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    assert dialog.bike_edit.text() == ""
    assert dialog.bike_edit.placeholderText() == "default: running - 4 (166 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running (170 bpm)"
    assert not dialog.save_button.isEnabled()


def test_the_placeholders_follow_the_running_threshold(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    dialog.threshold_box.setValue(160)

    assert dialog.bike_edit.placeholderText() == "default: running - 4 (156 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running (160 bpm)"


def test_a_set_value_is_shown_and_follows_the_day(qtbot: QtBot) -> None:
    dialog = dialog_of(
        qtbot,
        history_of(PhysiologyEntry(FIRST, athlete()), PhysiologyEntry(LATER, athlete(bike=160))),
    )

    assert dialog.bike_edit.text() == "160"
    dialog.valid_from_edit.setDate(QDate(2026, 2, 1))
    assert dialog.bike_edit.text() == ""


def test_typing_a_value_enables_save_and_says_what_applied_before(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    QTest.keyClicks(dialog.bike_edit, "158")

    assert dialog.save_button.isEnabled()
    assert dialog.note_text(EditField.BIKE_THRESHOLD) == "In force on this day: 166 bpm"


def test_the_field_accepts_only_digits(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    QTest.keyClicks(dialog.bike_edit, "1a5-8")

    assert dialog.bike_edit.text() == "158"


def test_an_out_of_range_value_is_reported_on_save_and_not_requested(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())
    requests: list[object] = []
    dialog.save_requested.connect(requests.append)
    QTest.keyClicks(dialog.other_edit, "30")

    dialog.request_save()

    assert requests == []
    assert "between 40 and 250" in dialog.error_text(EditField.OTHER_THRESHOLD)


def test_saving_emits_the_request_with_the_optional_threshold(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())
    requests: list[object] = []
    dialog.save_requested.connect(requests.append)
    QTest.keyClicks(dialog.bike_edit, "158")

    dialog.request_save()

    [request] = requests
    assert isinstance(request, MeasurementsRequest)
    assert request.update is not None
    assert request.update.bike_threshold == ThresholdHeartRate(158)


# --- Through the window: saved, listed and the load recalculated ------------------------------


def test_a_cycling_threshold_typed_in_the_settings_is_saved_listed_and_leaves_run_loads(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    loads_before = _loads(window)
    dialog = open_edit_dialog(qtbot, window)
    dialog.valid_from_edit.setDate(QDate(2020, 1, 1))
    QTest.keyClicks(dialog.bike_edit, "150")
    click(dialog.save_button)
    wait_profile_saved(qtbot, window)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    again = open_edit_dialog(qtbot, window)

    model = again.history_table.model()
    cells = [model.data(model.index(r, Column.BIKE_THRESHOLD)) for r in range(model.rowCount())]
    assert sorted(cells) == ["", "150"]  # the new row; the profile's first entry keeps the default
    # the folder holds only runs: they keep the running threshold (the rides are in the load tests)
    assert _loads(window) == loads_before
    assert first_load(window) > 0


def _loads(window: MainWindow) -> list[float]:
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    return sorted(a.load.trimp_eq for a in state.overview.activities)
