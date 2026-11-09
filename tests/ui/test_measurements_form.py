# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Form "Edit profile": values, changed fields and problems, without a window."""

from __future__ import annotations

from datetime import date

import pytest

from ponos_coach.load.domain import (
    HeartRateLimits,
    Physiology,
    PhysiologyEntry,
    Sex,
    ThresholdHeartRate,
)
from ponos_coach.profiles.domain import (
    BirthDate,
    FixedProfileData,
    Height,
    ProfileName,
    Weight,
    WeightEntry,
)
from ponos_coach.profiles.measurements import (
    MeasurementHistory,
    MeasurementProblem,
    ValidityPeriod,
)
from ponos_coach.ui.body_input import BodyProblem
from ponos_coach.ui.heart_rate_rules import HeartRateProblem
from ponos_coach.ui.measurements_form import (
    EditField,
    EditIssue,
    FieldChange,
    MeasurementSeries,
    MeasurementsForm,
    MeasurementsRequest,
    NameProblem,
    PendingPeriod,
    issue_for_rejection,
    weight_in_force,
)

TODAY = date(2026, 6, 30)
FIXED = FixedProfileData(Height(172), BirthDate(date(1992, 3, 4)))


def physiology(min_bpm: int, max_bpm: int, threshold: int, sex: Sex = Sex.FEMALE) -> Physiology:
    return Physiology(HeartRateLimits(min_bpm, max_bpm), ThresholdHeartRate(threshold), sex)


def make_history(*, fixed: FixedProfileData | None = FIXED) -> MeasurementHistory:
    return MeasurementHistory(
        fixed,
        (
            WeightEntry(date(2026, 1, 1), Weight(700)),
            WeightEntry(date(2026, 3, 1), Weight(690)),
        ),
        (
            PhysiologyEntry(date(2026, 1, 1), physiology(50, 190, 171)),
            PhysiologyEntry(date(2026, 3, 1), physiology(52, 188, 170)),
        ),
    )


def make_form(*, fixed: FixedProfileData | None = FIXED) -> MeasurementsForm:
    return MeasurementsForm(make_history(fixed=fixed), TODAY)


def changed(form: MeasurementsForm) -> bool:
    """`has_changes` read through a function: the type is not narrowed between changes."""
    return form.has_changes


def edited_weight(form: MeasurementsForm) -> str | None:
    """The written weight, read through a function: the type is not narrowed between two changes."""
    return form.edited_weight_text


def request_of(form: MeasurementsForm) -> MeasurementsRequest:
    result = form.build()
    assert isinstance(result, MeasurementsRequest), result
    return result


def test_the_day_is_today_and_the_values_are_the_ones_in_force() -> None:
    form = make_form()

    assert form.valid_from == TODAY
    assert (form.min_bpm, form.max_bpm, form.threshold_bpm) == (52, 188, 170)
    assert form.baseline_weight == Weight(690)
    assert form.sex is Sex.FEMALE
    assert not changed(form)


def test_an_untouched_form_has_nothing_to_save() -> None:
    assert make_form().build() == (
        EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),
    )


def test_untouched_fields_follow_the_chosen_day_and_touched_ones_stay() -> None:
    form = make_form()

    form.set_valid_from(date(2026, 2, 1))
    assert (form.min_bpm, form.max_bpm, form.threshold_bpm) == (50, 190, 171)
    assert form.baseline_weight == Weight(700)

    form.edit_threshold_bpm(160)
    form.set_valid_from(date(2026, 4, 1))
    assert form.threshold_bpm == 160  # touched: stays
    assert form.min_bpm == 52  # untouched: follows the day


def test_only_the_changed_fields_are_saved() -> None:
    form = make_form()
    form.edit_threshold_bpm(175)

    update = request_of(form).update

    assert update is not None
    assert update.valid_from == TODAY
    assert update.threshold_bpm == 175
    assert update.min_bpm is None
    assert update.max_bpm is None
    assert update.weight is None


def test_writing_the_same_value_as_the_one_in_force_is_not_a_change() -> None:
    form = make_form()
    form.edit_min_bpm(52)
    form.edit_weight("69,0")

    assert not changed(form)
    assert form.build() == (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)


def test_a_new_weight_accepts_comma_and_point_and_is_saved_with_the_chosen_day() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 5, 20))

    form.edit_weight("71,2")
    assert request_of(form).update is not None
    assert request_of(form).update.weight == Weight(712)  # type: ignore[union-attr]
    form.edit_weight("71.2")
    assert request_of(form).update.weight == Weight(712)  # type: ignore[union-attr]
    assert request_of(form).update.valid_from == date(2026, 5, 20)  # type: ignore[union-attr]


def test_a_blank_weight_means_unchanged_and_an_invalid_one_is_reported_on_the_field() -> None:
    form = make_form()
    form.edit_weight("   ")
    assert not changed(form)

    form.edit_weight("70,55")
    assert changed(form)  # there is something to fix: "Save" gets enabled and says what
    assert form.build() == (EditIssue(EditField.WEIGHT, BodyProblem.WEIGHT_INVALID),)


@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        (date(2026, 7, 1), TODAY),  # off point: tomorrow
        (TODAY, TODAY),  # on point: today
        (date(1900, 1, 1), date(1900, 1, 1)),  # on point
        (date(1899, 12, 31), date(1900, 1, 1)),  # off point
    ],
)
def test_the_day_is_brought_to_the_limit(requested: date, expected: date) -> None:
    form = make_form()

    form.set_valid_from(requested)

    assert form.valid_from == expected


def test_heart_rate_relations_are_reported_on_the_right_field() -> None:
    form = make_form()
    form.edit_min_bpm(190)
    assert EditIssue(EditField.MIN_BPM, HeartRateProblem.MIN_NOT_BELOW_MAX) in form.issues()

    form = make_form()
    form.edit_threshold_bpm(52)
    assert form.issues() == (
        EditIssue(EditField.THRESHOLD, HeartRateProblem.THRESHOLD_NOT_ABOVE_MIN),
    )
    form.edit_threshold_bpm(189)
    assert form.issues() == (EditIssue(EditField.THRESHOLD, HeartRateProblem.THRESHOLD_ABOVE_MAX),)


def test_a_profile_without_fixed_data_asks_to_complete_both_values_once() -> None:
    form = make_form(fixed=None)
    assert form.needs_fixed_data
    assert not changed(form)

    form.height_text = "170"
    assert changed(form)
    assert form.issues() == (EditIssue(EditField.BIRTH_DATE, BodyProblem.BIRTH_EMPTY),)

    form.birth_date = date(1990, 5, 1)
    request = request_of(form)
    assert request.update is None  # no measurement changed: only the fixed data
    assert request.fixed == FixedProfileData(Height(170), BirthDate(date(1990, 5, 1)))


def test_the_fixed_data_limits_are_the_ones_of_the_domain() -> None:
    form = make_form(fixed=None)
    form.height_text = "251"
    form.birth_date = date(2021, 7, 1)  # off point: one day past the limit

    assert form.issues() == (
        EditIssue(EditField.HEIGHT, BodyProblem.HEIGHT_INVALID),
        EditIssue(EditField.BIRTH_DATE, BodyProblem.BIRTH_INVALID),
    )
    form.height_text = "250"
    form.birth_date = date(2021, 6, 30)  # on point: exactly five years ago
    assert form.issues() == ()


def test_fixed_data_already_set_are_never_part_of_a_request() -> None:
    form = make_form()
    form.height_text = "180"  # ignored: the fixed data are not changed
    form.edit_threshold_bpm(175)

    assert request_of(form).fixed is None
    assert form.fixed == FIXED


def test_a_rejection_from_the_use_case_lands_on_the_matching_field() -> None:
    assert issue_for_rejection(MeasurementProblem.DATE_IN_FUTURE).field is EditField.VALID_FROM
    assert issue_for_rejection(MeasurementProblem.DATE_TOO_OLD).field is EditField.VALID_FROM
    assert issue_for_rejection(MeasurementProblem.INVALID_HEART_RATE).field is EditField.MIN_BPM
    assert issue_for_rejection(MeasurementProblem.NOTHING_TO_UPDATE).field is EditField.GENERAL


def test_the_weight_in_force_is_the_last_entry_not_after_the_day() -> None:
    history = make_history()

    assert weight_in_force(history, date(2025, 12, 31)) is None
    assert weight_in_force(history, date(2026, 1, 1)) == Weight(700)
    assert weight_in_force(history, date(2026, 2, 28)) == Weight(700)
    assert weight_in_force(history, date(2026, 3, 1)) == Weight(690)


def test_a_history_without_physiology_falls_back_to_the_defaults_instead_of_failing() -> None:
    form = MeasurementsForm(MeasurementHistory(FIXED, (), ()), TODAY)

    assert form.min_bpm < form.threshold_bpm <= form.max_bpm
    assert form.baseline_weight is None


# --- What changes and how long it applies (R6) ----------------------------------------------------


def test_the_changes_say_what_was_in_force_on_the_chosen_day() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 2, 1))
    form.edit_threshold_bpm(160)
    form.edit_weight("68,0")

    assert form.changes() == (
        FieldChange(EditField.WEIGHT, Weight(700)),
        FieldChange(EditField.THRESHOLD, 171),
    )


def test_changing_the_day_after_an_edit_recalculates_the_base_and_keeps_the_edit() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 2, 1))
    form.edit_threshold_bpm(160)
    assert form.changes() == (FieldChange(EditField.THRESHOLD, 171),)

    form.set_valid_from(date(2026, 5, 1))

    assert form.threshold_bpm == 160  # the written value stays
    assert form.changes() == (FieldChange(EditField.THRESHOLD, 170),)  # against the new base
    update = request_of(form).update
    assert update is not None
    assert (update.valid_from, update.threshold_bpm) == (date(2026, 5, 1), 160)


def test_a_value_written_back_to_the_one_in_force_is_untouched_and_follows_the_day() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 2, 1))
    form.edit_min_bpm(50)  # the value in force that day: not a change
    form.edit_weight("70,0")
    assert form.changes() == ()
    assert not changed(form)

    form.set_valid_from(date(2026, 5, 1))

    assert form.min_bpm == 52  # follows the new day instead of becoming a change to 50
    assert form.baseline_weight == Weight(690)
    assert edited_weight(form) is None
    assert not changed(form)


def test_a_cleared_weight_goes_back_to_following_the_day() -> None:
    form = make_form()
    form.edit_weight("71,0")
    assert edited_weight(form) == "71,0"

    form.edit_weight("")

    assert edited_weight(form) is None
    assert not changed(form)


def test_the_new_values_last_until_the_day_before_the_next_entry_of_their_series() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 2, 1))
    form.edit_weight("68,0")
    form.edit_threshold_bpm(160)

    periods = form.pending_periods()

    assert periods == (
        PendingPeriod(
            MeasurementSeries.WEIGHT, ValidityPeriod(date(2026, 2, 1), date(2026, 2, 28))
        ),
        PendingPeriod(
            MeasurementSeries.HEART_RATE, ValidityPeriod(date(2026, 2, 1), date(2026, 2, 28))
        ),
    )
    assert all(period.is_superseded for period in periods)


def test_the_series_are_independent_and_an_open_period_has_no_end() -> None:
    history = MeasurementHistory(
        FIXED,
        (WeightEntry(date(2026, 1, 1), Weight(700)),),  # no weight afterwards
        (
            PhysiologyEntry(date(2026, 1, 1), physiology(50, 190, 171)),
            PhysiologyEntry(date(2026, 3, 1), physiology(52, 188, 170)),
        ),
    )
    form = MeasurementsForm(history, TODAY)
    form.set_valid_from(date(2026, 2, 1))
    form.edit_weight("68,0")
    form.edit_threshold_bpm(160)

    weight, heart_rate = form.pending_periods()

    assert weight.period == ValidityPeriod(date(2026, 2, 1), None)
    assert not weight.is_superseded
    assert heart_rate.period == ValidityPeriod(date(2026, 2, 1), date(2026, 2, 28))


def test_the_same_day_as_an_existing_entry_replaces_it_and_is_not_superseded_by_it() -> None:
    form = make_form()
    form.set_valid_from(date(2026, 3, 1))
    form.edit_threshold_bpm(160)

    (heart_rate,) = form.pending_periods()

    assert heart_rate.period == ValidityPeriod(date(2026, 3, 1), None)


def test_an_untouched_form_has_no_pending_period() -> None:
    assert make_form().pending_periods() == ()


# --- Nome del profilo ----------------------------------------------------------------------------


def named_form(name: str = "Atleta di prova") -> MeasurementsForm:
    return MeasurementsForm(make_history(), TODAY, name)


def test_the_name_shown_is_the_current_one_until_it_is_edited() -> None:
    form = named_form()

    assert form.name_text == "Atleta di prova"
    assert not changed(form)
    form.edit_name("Altro")
    assert form.name_text == "Altro"
    assert changed(form)


def test_a_name_equal_to_the_current_one_is_not_a_change() -> None:
    form = named_form()

    form.edit_name("  Atleta di prova ")

    assert not changed(form)
    assert form.build() == (EditIssue(EditField.GENERAL, MeasurementProblem.NOTHING_TO_UPDATE),)


def test_a_new_name_is_saved_alone_normalised() -> None:
    form = named_form()

    form.edit_name("  Nuovo nome ")

    assert request_of(form) == MeasurementsRequest(None, None, ProfileName("Nuovo nome"))


def test_a_new_name_travels_with_the_measurements() -> None:
    form = named_form()
    form.edit_name("Nuovo nome")
    form.edit_weight("70,0")

    result = request_of(form)

    assert result.new_name == ProfileName("Nuovo nome")
    assert result.update is not None


@pytest.mark.parametrize(
    ("text", "problem"),
    [("", NameProblem.EMPTY), ("   ", NameProblem.EMPTY), ("due\nrighe", NameProblem.INVALID)],
)
def test_an_invalid_name_is_an_issue_on_the_name_field_and_blocks_the_save(
    text: str, problem: NameProblem
) -> None:
    form = named_form()

    form.edit_name(text)

    assert changed(form)  # "Salva" è attivo: l'errore si mostra al clic
    assert form.build() == (EditIssue(EditField.NAME, problem),)
