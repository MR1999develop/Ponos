# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Threshold test presentation: sentences, zone ranges, history rows and the undo command.

Nothing here opens a window. The values are made up (no real person). Numbers and dates are in
English format, whatever the language of the test computer.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtGui import QUndoStack
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import (
    NoTest,
    NotYet,
    Overdue,
    Recommended,
    SegmentSelection,
    ThresholdProtocol,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
    pace_zone_indices,
    pace_zone_starts_s_per_km,
)
from ponos_coach.load.domain import zone_starts_bpm
from ponos_coach.ui.threshold_history_model import (
    SORT_ROLE,
    HistoryColumn,
    ThresholdHistoryModel,
    sorted_history_model,
)
from ponos_coach.ui.threshold_loader import AppliedCell
from ponos_coach.ui.threshold_presentation import (
    ActivityBrief,
    HistoryEntry,
    ThresholdReview,
    can_be_threshold_test,
    heart_rate_zone_ranges,
    history_row,
    pace_text,
    pace_zone_ranges,
    present_review,
    protocol_choice_text,
    protocol_method_text,
    rejection_message,
    retest_status_bar_text,
    retest_text,
)
from ponos_coach.ui.threshold_undo import ApplyThresholdTestCommand
from tests.builders import fit_identity
from tests.ui.threshold_support import plain

ROME = ZoneInfo("Europe/Rome")
START = datetime(2026, 3, 14, 7, 10, tzinfo=UTC)
THRESHOLD_BPM = 168
THRESHOLD_PACE = 278


@pytest.fixture(autouse=True)
def english_numbers(qtbot: QtBot) -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def a_result(
    *,
    pace: int | None = THRESHOLD_PACE,
    number: int = 1,
    protocol: ThresholdProtocol = ThresholdProtocol.THIRTY_MINUTES,
) -> ThresholdTestResult:
    return ThresholdTestResult(
        identity=fit_identity(number),
        test_day=date(2026, 3, 14),
        selection=SegmentSelection.BY_DISTANCE
        if pace is not None
        else SegmentSelection.BY_HEART_RATE,
        segment_start_s=600.0,
        segment_start=START,
        threshold_bpm=THRESHOLD_BPM,
        pace_s_per_km=pace,
        valid_hr_samples=1200,
        hr_zone_starts_bpm=zone_starts_bpm(THRESHOLD_BPM),
        pace_zone_starts_s_per_km=None if pace is None else pace_zone_starts_s_per_km(pace),
        protocol=protocol,
    )


# --- Zone ranges ------------------------------------------------------------------------------


def test_heart_rate_zones_are_whole_bpm_ranges_that_do_not_overlap() -> None:
    ranges = heart_rate_zone_ranges(zone_starts_bpm(THRESHOLD_BPM))

    assert ranges == (
        "Up to 125",
        "126 to 142",
        "143 to 159",
        "160 to 173",
        "174 or more",
    )


def test_a_boundary_that_is_a_whole_bpm_belongs_to_the_faster_zone() -> None:
    # 75% of 200 is 150 exactly: 150 is the first bpm of Z2, not the last of Z1
    ranges = heart_rate_zone_ranges(zone_starts_bpm(200))

    assert ranges[0] == "Up to 149"
    assert ranges[1].startswith("150 to")


def test_pace_zones_agree_with_the_domain_on_every_whole_second() -> None:
    ranges = pace_zone_ranges(pace_zone_starts_s_per_km(THRESHOLD_PACE))

    assert ranges == (
        "Slower than 5:56",
        "5:16 to 5:56",
        "4:56 to 5:15",
        "4:39 to 4:55",
        "4:38 or faster",
    )
    paces = np.arange(200.0, 500.0)
    zones = pace_zone_indices(paces, THRESHOLD_PACE)
    for pace, zone in zip(paces, zones, strict=True):
        shown = pace_text(int(pace))
        owner = next(i for i, text in enumerate(ranges) if _contains(text, int(pace)))
        assert owner == zone, shown


def _contains(text: str, pace: int) -> bool:
    """Whether the sentence of a pace range covers `pace` (whole seconds per km)."""

    def seconds(clock: str) -> int:
        minutes, secs = clock.split(":")
        return int(minutes) * 60 + int(secs)

    if text.startswith("Slower than "):
        return pace > seconds(text.removeprefix("Slower than "))
    if text.endswith(" or faster"):
        return pace <= seconds(text.removesuffix(" or faster"))
    fast, slow = text.split(" to ")
    return seconds(fast) <= pace <= seconds(slow)


def test_a_pace_is_minutes_and_two_digit_seconds() -> None:
    assert pace_text(278) == "4:38"
    assert pace_text(305) == "5:05"


# --- The review -------------------------------------------------------------------------------


def test_the_review_gives_day_segment_threshold_pace_current_and_five_zones() -> None:
    view = present_review(ThresholdReview(a_result(), 171), ROME)

    assert view.day == "3/14/26"
    assert plain(view.segment_start) == "8:10 AM"  # 07:10 UTC is 08:10 in Rome
    assert view.selection == "The 30 minutes in which you covered the most distance"
    assert (view.threshold, view.pace, view.current) == ("168", "4:38", "171")
    assert view.zone_headers == ("Zone", "Heart rate (bpm)", "Pace (min:s per km)")
    assert [row[0] for row in view.zone_rows] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert all(len(row) == 3 for row in view.zone_rows)


def test_without_distance_the_review_explains_the_missing_pace_and_has_no_pace_column() -> None:
    view = present_review(ThresholdReview(a_result(pace=None), 171), ROME)

    assert view.pace == "Not available: the activity has no usable distance."
    assert view.selection.startswith("The 30 minutes with the highest heart rate")
    assert view.zone_headers == ("Zone", "Heart rate (bpm)")
    assert all(len(row) == 2 for row in view.zone_rows)


def test_the_review_says_which_protocol_it_was_and_uses_its_minutes() -> None:
    thirty = present_review(ThresholdReview(a_result(), 171), ROME)
    twenty = present_review(
        ThresholdReview(a_result(protocol=ThresholdProtocol.TWENTY_MINUTES), 171), ROME
    )

    assert thirty.protocol == "30-minute test (last 20 minutes)"
    assert twenty.protocol == "20-minute test (\u22125%)"
    assert twenty.selection == "The 20 minutes in which you covered the most distance"


def test_the_protocol_texts_are_the_same_in_the_choice_and_in_the_history() -> None:
    assert protocol_choice_text(ThresholdProtocol.THIRTY_MINUTES) == (
        "30-minute test (last 20 minutes)"
    )
    assert protocol_method_text(ThresholdProtocol.TWENTY_MINUTES, 2) == "20-minute test, method 2"


# --- Rejections -------------------------------------------------------------------------------


@pytest.mark.parametrize("rejection", list(ThresholdRejection))
def test_every_rejection_says_what_is_wrong_and_what_to_do(rejection: ThresholdRejection) -> None:
    message = rejection_message(rejection)

    assert message.title and message.what and message.remedy
    assert "{" not in message.what + message.remedy  # no placeholder left open
    assert "Traceback" not in message.what


def test_a_short_activity_is_told_in_plain_words_with_the_minutes_of_the_protocol() -> None:
    message = rejection_message(ThresholdRejection.TOO_SHORT)

    assert message.what == "The activity is shorter than 30 minutes."


def test_a_short_activity_for_the_twenty_minute_test_names_twenty_minutes() -> None:
    message = rejection_message(ThresholdRejection.TOO_SHORT, ThresholdProtocol.TWENTY_MINUTES)

    assert message.what == "The activity is shorter than 20 minutes."


# --- Retest status ----------------------------------------------------------------------------


def test_the_retest_sentences() -> None:
    first, last = date(2026, 4, 25), date(2026, 5, 9)

    assert retest_text(NoTest()) == "No test yet"
    assert retest_text(NotYet(first, last)) == "Next test from 4/25/26 to 5/9/26"
    assert retest_text(Recommended(first, last)) == (
        "A new test is recommended from 4/25/26 to 5/9/26"
    )
    assert (
        retest_text(Overdue(1, last)) == "The last test is 57 days old: a new test is recommended"
    )
    assert retest_status_bar_text(NoTest()) == "Threshold test: No test yet"


# --- History ----------------------------------------------------------------------------------


def a_test(day: date, bpm: int = 168, pace: int | None = 278, number: int = 1) -> ThresholdTest:
    return ThresholdTest(fit_identity(number), day, START, bpm, pace)


def test_a_history_row_has_texts_and_numeric_keys() -> None:
    entry = HistoryEntry(a_test(date(2026, 3, 14)), ActivityBrief(Sport.RUNNING, START))

    row = history_row(entry, ROME)

    assert row.cells[0] == "3/14/26"
    assert plain(row.cells[1]) == "Running, 3/14/26 8:10 AM"
    assert row.cells[2:] == ("168", "4:38", "30-minute test, method 2")
    assert row.keys[0] == float(date(2026, 3, 14).toordinal())
    assert row.keys[2:4] == (168.0, 278.0)


def test_a_history_row_names_the_protocol_of_the_test() -> None:
    test = ThresholdTest(
        fit_identity(1),
        date(2026, 3, 14),
        START,
        168,
        278,
        protocol=ThresholdProtocol.TWENTY_MINUTES,
    )

    row = history_row(HistoryEntry(test, None), ROME)

    assert row.cells[4] == "20-minute test, method 2"


def test_a_test_without_pace_or_activity_leaves_the_pace_empty_and_says_the_activity_is_gone() -> (
    None
):
    row = history_row(HistoryEntry(a_test(date(2026, 3, 14), pace=None), None), ROME)

    assert row.cells[1] == "No longer in the profile"
    assert row.cells[3] == ""
    assert row.keys[3] == -1.0


def test_the_history_table_shows_the_latest_test_first_and_aligns_numbers_on_the_right(
    qtbot: QtBot,
) -> None:
    older = HistoryEntry(a_test(date(2026, 1, 10), 160, 300, 1), None)
    newer = HistoryEntry(a_test(date(2026, 3, 14), 168, 278, 2), None)
    model = ThresholdHistoryModel()
    proxy = sorted_history_model(model)

    model.set_entries([older, newer], ROME)

    shown = [proxy.index(row, HistoryColumn.THRESHOLD).data() for row in range(2)]
    assert shown == ["168", "160"]
    alignment = proxy.index(0, HistoryColumn.THRESHOLD).data(Qt.ItemDataRole.TextAlignmentRole)
    assert int(alignment) & int(Qt.AlignmentFlag.AlignRight)
    left = proxy.index(0, HistoryColumn.ACTIVITY).data(Qt.ItemDataRole.TextAlignmentRole)
    assert int(left) & int(Qt.AlignmentFlag.AlignLeft)
    assert proxy.index(0, HistoryColumn.PACE).data(SORT_ROLE) == 278.0
    assert proxy.index(0, HistoryColumn.DAY).data(SORT_ROLE) == float(date(2026, 3, 14).toordinal())


def test_only_runs_can_be_the_test() -> None:
    allowed = {sport for sport in Sport if can_be_threshold_test(sport)}

    assert allowed == {Sport.RUNNING, Sport.TRAIL}


# --- The undo command -------------------------------------------------------------------------


class RecordingOps:
    """Fake of the jobs a command starts: it records what it was asked to do."""

    def __init__(self, *, accepts: bool = True) -> None:
        self.calls: list[str] = []
        self._accepts = accepts

    def reapply(self, _result: ThresholdTestResult, _cell: AppliedCell) -> bool:
        self.calls.append("reapply")
        return self._accepts

    def remove(self, _cell: AppliedCell) -> bool:
        self.calls.append("remove")
        return self._accepts


def test_pushing_the_command_does_not_apply_again_then_undo_and_redo_do(qtbot: QtBot) -> None:
    ops = RecordingOps()
    stack = QUndoStack()
    command = ApplyThresholdTestCommand(ops, a_result(), AppliedCell(), lambda: None)

    stack.push(command)  # the test was already applied by the dialog
    assert ops.calls == []
    assert stack.undoText() == "Apply threshold test"

    stack.undo()
    stack.redo()

    assert ops.calls == ["remove", "reapply"]


def test_a_refused_undo_and_a_refused_redo_call_the_failure_callback(qtbot: QtBot) -> None:
    refused: list[str] = []
    ops = RecordingOps(accepts=False)
    stack = QUndoStack()
    stack.push(
        ApplyThresholdTestCommand(ops, a_result(), AppliedCell(), lambda: refused.append("refused"))
    )

    stack.undo()
    stack.redo()

    assert ops.calls == ["remove", "reapply"]
    assert refused == ["refused", "refused"]
