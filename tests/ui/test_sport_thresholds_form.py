# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""LT1 and LT2 of the two groups in "Edit profile": form model, history columns and dialog.
All the values are made up (no real person)."""

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


def athlete(
    run_lt1: int | None = None,
    other: int | None = None,
    other_lt1: int | None = None,
    threshold: int = 170,
) -> Physiology:
    def opt(bpm: int | None) -> ThresholdHeartRate | None:
        return None if bpm is None else ThresholdHeartRate(bpm)

    return Physiology(
        HeartRateLimits(50, 190),
        ThresholdHeartRate(threshold),
        Sex.MALE,
        other_threshold=opt(other),
        run_lt1=opt(run_lt1),
        other_lt1=opt(other_lt1),
    )


def history_of(*entries: PhysiologyEntry) -> MeasurementHistory:
    return MeasurementHistory(None, (), entries)


def default_history() -> MeasurementHistory:
    return history_of(PhysiologyEntry(FIRST, athlete()))


def form_with_lt1_in_force() -> MeasurementsForm:
    return MeasurementsForm(
        history_of(PhysiologyEntry(FIRST, athlete()), PhysiologyEntry(LATER, athlete(run_lt1=140))),
        TODAY,
        PROFILE,
    )


def request_of(form: MeasurementsForm) -> MeasurementsRequest:
    result = form.build()
    assert isinstance(result, MeasurementsRequest), result
    return result


# --- Form model -----------------------------------------------------------------------------


def test_unset_values_are_empty_and_show_their_defaults() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    for field in (EditField.RUN_LT1, EditField.OTHER_THRESHOLD, EditField.OTHER_LT1):
        assert form.threshold_text(field) == ""
    assert form.threshold_default(EditField.RUN_LT1) == 141  # 83% of 170 = 141.1
    assert form.threshold_default(EditField.OTHER_THRESHOLD) == 170  # the running LT2
    assert form.threshold_default(EditField.OTHER_LT1) == 141
    assert not form.has_changes


def test_the_defaults_follow_the_running_lt2_being_typed() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_threshold_bpm(160)

    assert form.threshold_default(EditField.RUN_LT1) == 133  # 132.8
    assert form.threshold_default(EditField.OTHER_THRESHOLD) == 160
    assert form.threshold_default(EditField.OTHER_LT1) == 133


def test_the_default_other_lt1_follows_the_other_lt2_being_typed() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold("150")

    assert form.threshold_default(EditField.OTHER_LT1) == 125  # 124.5: half rounds up
    assert form.threshold_default(EditField.RUN_LT1) == 141


def test_typed_values_are_saved_each_on_its_own_and_the_others_stay_untouched() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_run_lt1("140")
    form.edit_other_threshold("150")
    form.edit_other_lt1("120")

    update = request_of(form).update
    assert update is not None
    assert update.run_lt1 == ThresholdHeartRate(140)
    assert update.other_threshold == ThresholdHeartRate(150)
    assert update.other_lt1 == ThresholdHeartRate(120)
    assert update.threshold_bpm is None


def test_the_change_says_the_value_that_applied_that_day() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_run_lt1("140")
    form.edit_other_threshold("150")
    form.edit_other_lt1("120")

    changes = form.changes()
    assert FieldChange(EditField.RUN_LT1, 141) in changes  # the default applied
    assert FieldChange(EditField.OTHER_THRESHOLD, 170) in changes  # the running LT2
    assert FieldChange(EditField.OTHER_LT1, 141) in changes


def test_a_value_equal_to_the_one_in_force_is_not_a_change() -> None:
    form = form_with_lt1_in_force()

    form.edit_run_lt1("140")

    assert not form.has_changes
    assert form.build() == (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)


def test_emptying_a_set_value_goes_back_to_the_default() -> None:
    form = form_with_lt1_in_force()

    form.edit_run_lt1("")

    update = request_of(form).update
    assert update is not None
    assert update.run_lt1 is ThresholdReset.DEFAULT


def test_an_empty_field_of_an_unset_value_is_not_a_change() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_run_lt1("")

    assert not form.has_changes


def test_untouched_optional_fields_follow_the_chosen_day() -> None:
    form = form_with_lt1_in_force()

    assert form.threshold_text(EditField.RUN_LT1) == "140"
    form.set_valid_from(date(2026, 2, 1))
    assert form.threshold_text(EditField.RUN_LT1) == ""


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("39", HeartRateProblem.NOT_A_THRESHOLD),
        ("251", HeartRateProblem.NOT_A_THRESHOLD),
        ("abc", HeartRateProblem.NOT_A_THRESHOLD),
        ("50", HeartRateProblem.LT1_NOT_ABOVE_MIN),
        ("170", HeartRateProblem.LT1_NOT_BELOW_LT2),
        ("171", HeartRateProblem.LT1_NOT_BELOW_LT2),
    ],
)
def test_an_invalid_lt1_is_reported_on_its_field(text: str, problem: HeartRateProblem) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_run_lt1(text)

    assert form.issues() == (EditIssue(EditField.RUN_LT1, problem),)
    assert form.has_changes  # a value to correct enables Save, which then shows the problem


@pytest.mark.parametrize(("text", "ok"), [("51", True), ("169", True), ("170", False)])
def test_the_limit_values_of_an_lt1_on_and_off_points(text: str, ok: bool) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_run_lt1(text)

    assert (form.issues() == ()) is ok


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("50", HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN),
        ("191", HeartRateProblem.THRESHOLD_ABOVE_MAX),
        ("39", HeartRateProblem.NOT_A_THRESHOLD),
    ],
)
def test_an_invalid_other_lt2_is_reported_on_its_field(
    text: str, problem: HeartRateProblem
) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold(text)

    assert form.issues() == (EditIssue(EditField.OTHER_THRESHOLD, problem),)


@pytest.mark.parametrize("text", ["51", "190"])
def test_the_limit_values_of_the_other_lt2_are_accepted(text: str) -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold(text)

    assert form.issues() == ()


def test_the_other_lt1_is_checked_against_the_other_lt2_typed() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_threshold("150")
    form.edit_other_lt1("150")

    assert form.issues() == (EditIssue(EditField.OTHER_LT1, HeartRateProblem.LT1_NOT_BELOW_LT2),)
    form.edit_other_lt1("149")
    assert form.issues() == ()


def test_lowering_the_other_lt2_below_an_lt1_in_force_is_reported_on_the_lt1() -> None:
    form = MeasurementsForm(
        history_of(PhysiologyEntry(FIRST, athlete(other=160, other_lt1=140))), TODAY, PROFILE
    )

    form.edit_other_threshold("140")

    assert form.issues() == (EditIssue(EditField.OTHER_LT1, HeartRateProblem.LT1_NOT_BELOW_LT2),)


def test_lowering_the_running_lt2_below_the_run_lt1_in_force_is_reported_on_the_lt1() -> None:
    form = form_with_lt1_in_force()

    form.edit_threshold_bpm(140)

    assert EditIssue(EditField.RUN_LT1, HeartRateProblem.LT1_NOT_BELOW_LT2) in form.issues()


def test_raising_the_minimum_to_an_lt1_in_force_is_reported_on_the_lt1() -> None:
    form = form_with_lt1_in_force()

    form.edit_min_bpm(140)

    assert EditIssue(EditField.RUN_LT1, HeartRateProblem.LT1_NOT_ABOVE_MIN) in form.issues()


def test_lowering_the_maximum_below_an_entered_other_lt2_is_reported_on_that_field() -> None:
    form = MeasurementsForm(history_of(PhysiologyEntry(FIRST, athlete(other=160))), TODAY, PROFILE)

    form.edit_max_bpm(155)

    assert EditIssue(EditField.OTHER_THRESHOLD, HeartRateProblem.THRESHOLD_ABOVE_MAX) in (
        form.issues()
    )


def test_an_lt1_alone_makes_a_heart_rate_period() -> None:
    form = MeasurementsForm(default_history(), TODAY, PROFILE)

    form.edit_other_lt1("120")

    [pending] = form.pending_periods()
    assert pending.period.valid_from == TODAY


# --- History table -------------------------------------------------------------------------


def test_the_history_has_a_column_per_value_and_empty_cells_for_the_defaults(
    qtbot: QtBot,
) -> None:
    model = MeasurementHistoryModel()
    model.set_history(
        history_of(
            PhysiologyEntry(FIRST, athlete()),
            PhysiologyEntry(LATER, athlete(run_lt1=140, other=150, other_lt1=120)),
        )
    )
    columns = (Column.RUN_LT1, Column.THRESHOLD, Column.OTHER_LT1, Column.OTHER_THRESHOLD)

    cells = [
        [model.data(model.index(row, column)) for column in columns]
        for row in range(model.rowCount())
    ]
    headers = [
        model.headerData(c, Qt.Orientation.Horizontal, Qt.ItemDataRole.DisplayRole) for c in columns
    ]

    assert cells == [["", "170", "", ""], ["140", "170", "120", "150"]]
    assert headers == [
        "Running LT1 (bpm)",
        "Running LT2 (bpm)",
        "Other sports LT1 (bpm)",
        "Other sports LT2 (bpm)",
    ]


# --- Dialog -----------------------------------------------------------------------------------


def dialog_of(qtbot: QtBot, history: MeasurementHistory) -> ProfileEditDialog:
    dialog = ProfileEditDialog(MeasurementsForm(history, TODAY, PROFILE), history, Theme.LIGHT)
    qtbot.addWidget(dialog)
    return dialog


def test_the_empty_fields_show_the_defaults_as_placeholders(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    assert dialog.run_lt1_edit.text() == ""
    assert dialog.run_lt1_edit.placeholderText() == "default: 83% of LT2 (141 bpm)"
    assert dialog.other_lt1_edit.placeholderText() == "default: 83% of LT2 (141 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running LT2 (170 bpm)"
    assert not dialog.save_button.isEnabled()


def test_the_placeholders_follow_the_running_lt2(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    dialog.threshold_box.setValue(160)

    assert dialog.run_lt1_edit.placeholderText() == "default: 83% of LT2 (133 bpm)"
    assert dialog.other_edit.placeholderText() == "default: running LT2 (160 bpm)"


def test_the_other_lt1_placeholder_follows_the_other_lt2_typed(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    QTest.keyClicks(dialog.other_edit, "150")

    assert dialog.other_lt1_edit.placeholderText() == "default: 83% of LT2 (125 bpm)"


def test_each_lt1_and_lt2_explains_itself_in_a_tooltip(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    for lt1 in (dialog.run_lt1_edit, dialog.other_lt1_edit):
        assert "aerobic threshold" in lt1.toolTip()
        assert "talk" in lt1.toolTip()
    for lt2 in (dialog.threshold_box, dialog.other_edit):
        assert "lactate threshold" in lt2.toolTip()


def test_the_fields_of_the_two_groups_have_distinct_accessible_names(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    names = {
        w.accessibleName()
        for w in (
            dialog.run_lt1_edit,
            dialog.threshold_box,
            dialog.other_lt1_edit,
            dialog.other_edit,
        )
    }

    assert len(names) == 4


def test_a_set_value_is_shown_and_follows_the_day(qtbot: QtBot) -> None:
    dialog = dialog_of(
        qtbot,
        history_of(PhysiologyEntry(FIRST, athlete()), PhysiologyEntry(LATER, athlete(run_lt1=140))),
    )

    assert dialog.run_lt1_edit.text() == "140"
    dialog.valid_from_edit.setDate(QDate(2026, 2, 1))
    assert dialog.run_lt1_edit.text() == ""


def test_typing_a_value_enables_save_and_says_what_applied_before(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    QTest.keyClicks(dialog.run_lt1_edit, "140")

    assert dialog.save_button.isEnabled()
    assert dialog.note_text(EditField.RUN_LT1) == "In force on this day: 141 bpm"


def test_the_field_accepts_only_digits(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())

    QTest.keyClicks(dialog.run_lt1_edit, "1a4-0")

    assert dialog.run_lt1_edit.text() == "140"


def test_an_out_of_range_value_is_reported_on_save_and_not_requested(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())
    requests: list[object] = []
    dialog.save_requested.connect(requests.append)
    QTest.keyClicks(dialog.other_edit, "30")

    dialog.request_save()

    assert requests == []
    assert "between 40 and 250" in dialog.error_text(EditField.OTHER_THRESHOLD)


def test_an_lt1_not_below_lt2_is_reported_on_save_and_not_requested(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())
    requests: list[object] = []
    dialog.save_requested.connect(requests.append)
    QTest.keyClicks(dialog.run_lt1_edit, "170")

    dialog.request_save()

    assert requests == []
    assert dialog.error_text(EditField.RUN_LT1) == "LT1 must be lower than LT2."


def test_saving_emits_the_request_with_the_optional_values(qtbot: QtBot) -> None:
    dialog = dialog_of(qtbot, default_history())
    requests: list[object] = []
    dialog.save_requested.connect(requests.append)
    QTest.keyClicks(dialog.run_lt1_edit, "140")
    QTest.keyClicks(dialog.other_lt1_edit, "120")
    QTest.keyClicks(dialog.other_edit, "150")

    dialog.request_save()

    [request] = requests
    assert isinstance(request, MeasurementsRequest)
    assert request.update is not None
    assert request.update.run_lt1 == ThresholdHeartRate(140)
    assert request.update.other_lt1 == ThresholdHeartRate(120)
    assert request.update.other_threshold == ThresholdHeartRate(150)


# --- Through the window: saved, listed and the load recalculated ------------------------------


def test_an_lt1_typed_in_the_settings_is_saved_listed_and_leaves_the_loads(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    loads_before = _loads(window)
    dialog = open_edit_dialog(qtbot, window)
    dialog.valid_from_edit.setDate(QDate(2020, 1, 1))
    QTest.keyClicks(dialog.run_lt1_edit, "140")
    click(dialog.save_button)
    wait_profile_saved(qtbot, window)
    qtbot.waitUntil(lambda: isinstance(window.controller.overview_state, Ready), timeout=WAIT_MS)

    again = open_edit_dialog(qtbot, window)

    model = again.history_table.model()
    cells = [model.data(model.index(r, Column.RUN_LT1)) for r in range(model.rowCount())]
    assert sorted(cells) == ["", "140"]  # the new row; the profile's first entry keeps the default
    # an LT1 never changes a load (it only classifies seconds in the activity detail)
    assert _loads(window) == loads_before
    assert first_load(window) > 0


def _loads(window: MainWindow) -> list[float]:
    state = window.controller.overview_state
    assert isinstance(state, Ready)
    return sorted(a.load.trimp_eq for a in state.overview.activities)
