# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Presentation of the threshold test, testable without a window.

The domain gives categories and numbers (a result, a rejection, a retest status); here they become
the values and the sentences the dialogs show. Each sentence is whole, so the translation never
composes fragments. Threshold, pace and zones are health data: they serve only the window, never
the logs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from itertools import pairwise
from zoneinfo import ZoneInfo

from PySide6.QtCore import QCoreApplication, QDate, QDateTime, QLocale, QTime

from ponos_coach.activities.domain import Sport
from ponos_coach.assessment.domain import (
    DEFAULT_PROTOCOL,
    NoTest,
    NotYet,
    Overdue,
    Recommended,
    RetestStatus,
    SegmentSelection,
    ThresholdProtocol,
    ThresholdRejection,
    ThresholdTest,
    ThresholdTestResult,
)
from ponos_coach.assessment.domain.defaults import (
    MAX_PAUSE_S,
    RETEST_MAX_DAYS,
    THRESHOLD_TEST_SPORTS,
    THRESHOLD_WINDOW_S,
)
from ponos_coach.load.use_cases import ExcludedActivity
from ponos_coach.profiles.measurements import ValidityPeriod
from ponos_coach.ui.activity_text import sport_text
from ponos_coach.ui.errors import ErrorMessage
from ponos_coach.ui.measurement_texts import saved_period_text
from ponos_coach.ui.measurements_form import MeasurementSeries

# Context "ThresholdTexts" written as a literal: lupdate does not follow aliases.

_SECONDS_PER_MINUTE = 60
_NOISE_DECIMALS = 6
_ZONE_COUNT = 5

type ZoneRows = tuple[tuple[str, ...], ...]


# --- What the data thread hands over ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThresholdReview:
    """An accepted test and, to compare, the threshold in force today (bpm)."""

    result: ThresholdTestResult
    current_bpm: int


type ThresholdVerdict = ThresholdReview | ThresholdRejection | ExcludedActivity
"""What evaluating an activity gives: a test to review, why it is rejected, or why its file is
unusable."""


@dataclass(frozen=True, slots=True)
class ActivityBrief:
    """What the history says about the activity of a test (it may be gone from the index)."""

    sport: Sport
    start: datetime


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """A test of the history and its activity, if the profile still has it."""

    test: ThresholdTest
    activity: ActivityBrief | None


@dataclass(frozen=True, slots=True)
class ThresholdHistory:
    """The tests applied so far and where today stands against the retest window."""

    entries: tuple[HistoryEntry, ...]
    retest: RetestStatus


# --- Rules ------------------------------------------------------------------------------------


def can_be_threshold_test(sport: Sport) -> bool:
    """True if an activity of this sport can be used as the threshold test (a run)."""
    return sport in THRESHOLD_TEST_SPORTS


def not_a_run_reason() -> str:
    """For a tooltip: why the activity cannot be the test."""
    return QCoreApplication.translate(
        "ThresholdTexts", "Only a run can be used as a threshold test."
    )


# --- Numbers and dates ------------------------------------------------------------------------


def date_text(day: date) -> str:
    """A date in the locale's short format."""
    return QLocale().toString(QDate(day.year, day.month, day.day), QLocale.FormatType.ShortFormat)


def _time_text(moment: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return QLocale().toString(
        QTime(local.hour, local.minute, local.second), QLocale.FormatType.ShortFormat
    )


def _date_time_text(moment: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return QLocale().toString(
        QDateTime(
            QDate(local.year, local.month, local.day),
            QTime(local.hour, local.minute, local.second),
        ),
        QLocale.FormatType.ShortFormat,
    )


def pace_text(seconds_per_km: int) -> str:
    """A pace as `m:ss` (per kilometre)."""
    minutes, seconds = divmod(seconds_per_km, _SECONDS_PER_MINUTE)
    return f"{minutes}:{seconds:02d}"


# --- The two protocols -------------------------------------------------------------------------


def protocol_minutes(protocol: ThresholdProtocol) -> int:
    """Length of the test of a protocol, in minutes."""
    return protocol.duration_s // _SECONDS_PER_MINUTE


def protocol_choice_text(protocol: ThresholdProtocol) -> str:
    """The protocol as the user chooses it (a radio button label)."""
    translate = QCoreApplication.translate
    if protocol is ThresholdProtocol.THIRTY_MINUTES:
        return translate("ThresholdTexts", "30-minute test (last 20 minutes)")
    return translate("ThresholdTexts", "20-minute test (−5%)")  # noqa: RUF001 - a real minus sign


def protocol_method_text(protocol: ThresholdProtocol, method_version: int) -> str:
    """Protocol and method version of a saved test, for the history table."""
    translate = QCoreApplication.translate
    version = QLocale().toString(method_version)
    if protocol is ThresholdProtocol.THIRTY_MINUTES:
        return translate("ThresholdTexts", "30-minute test, method {version}").format(
            version=version
        )
    return translate("ThresholdTexts", "20-minute test, method {version}").format(version=version)


# --- The review of a test ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewView:
    """Everything the review dialog shows for an accepted test."""

    day: str
    protocol: str
    segment_start: str
    selection: str
    threshold: str
    pace: str
    current: str
    zone_headers: tuple[str, ...]
    zone_rows: ZoneRows


def _selection_text(selection: SegmentSelection, protocol: ThresholdProtocol) -> str:
    minutes = QLocale().toString(protocol_minutes(protocol))
    if selection is SegmentSelection.BY_DISTANCE:
        return QCoreApplication.translate(
            "ThresholdTexts", "The {minutes} minutes in which you covered the most distance"
        ).format(minutes=minutes)
    return QCoreApplication.translate(
        "ThresholdTexts",
        "The {minutes} minutes with the highest heart rate (the activity has no usable distance)",
    ).format(minutes=minutes)


def _first_whole(value: float) -> int:
    """The smallest whole number not below `value`, ignoring floating-point noise."""
    return math.ceil(round(value, _NOISE_DECIMALS))


def _last_whole(value: float) -> int:
    """The largest whole number not above `value`, ignoring floating-point noise."""
    return math.floor(round(value, _NOISE_DECIMALS))


def heart_rate_zone_ranges(starts_bpm: tuple[float, float, float, float]) -> tuple[str, ...]:
    """The whole-bpm range of Z1 to Z5 from the HR at which Z2 to Z5 start."""
    edges = [_first_whole(start) for start in starts_bpm]
    locale = QLocale()
    translate = QCoreApplication.translate
    ranges = [
        translate("ThresholdTexts", "Up to {high}").format(high=locale.toString(edges[0] - 1))
    ]
    ranges.extend(
        translate("ThresholdTexts", "{low} to {high}").format(
            low=locale.toString(low), high=locale.toString(next_edge - 1)
        )
        for low, next_edge in pairwise(edges)
    )
    ranges.append(
        translate("ThresholdTexts", "{low} or more").format(low=locale.toString(edges[-1]))
    )
    return tuple(ranges)


def pace_zone_ranges(starts_s_per_km: tuple[float, float, float, float]) -> tuple[str, ...]:
    """The whole-second pace range of Z1 to Z5 (`m:ss` per km, the faster pace first).

    A pace at or under each start is in that zone or a faster one; Z1 is slower than the first.
    """
    slowest = [_last_whole(start) for start in starts_s_per_km]
    translate = QCoreApplication.translate
    ranges = [translate("ThresholdTexts", "Slower than {pace}").format(pace=pace_text(slowest[0]))]
    ranges.extend(
        translate("ThresholdTexts", "{fast} to {slow}").format(
            fast=pace_text(faster_edge + 1), slow=pace_text(slow)
        )
        for slow, faster_edge in pairwise(slowest)
    )
    ranges.append(
        translate("ThresholdTexts", "{fast} or faster").format(fast=pace_text(slowest[-1]))
    )
    return tuple(ranges)


def present_review(review: ThresholdReview, zone: ZoneInfo) -> ReviewView:
    """The values the review dialog shows; the zone table has a pace column only with a pace."""
    result = review.result
    translate = QCoreApplication.translate
    locale = QLocale()
    names = tuple(f"Z{number}" for number in range(1, _ZONE_COUNT + 1))
    heart_rate = heart_rate_zone_ranges(result.hr_zone_starts_bpm)
    headers: tuple[str, ...] = (
        translate("ThresholdTexts", "Zone"),
        translate("ThresholdTexts", "Heart rate (bpm)"),
    )
    columns: list[tuple[str, ...]] = [names, heart_rate]
    if result.pace_s_per_km is None or result.pace_zone_starts_s_per_km is None:
        pace = translate("ThresholdTexts", "Not available: the activity has no usable distance.")
    else:
        pace = pace_text(result.pace_s_per_km)
        headers += (translate("ThresholdTexts", "Pace (min:s per km)"),)
        columns.append(pace_zone_ranges(result.pace_zone_starts_s_per_km))
    return ReviewView(
        day=date_text(result.test_day),
        protocol=protocol_choice_text(result.protocol),
        segment_start=_time_text(result.segment_start, zone),
        selection=_selection_text(result.selection, result.protocol),
        threshold=locale.toString(result.threshold_bpm),
        pace=pace,
        current=locale.toString(review.current_bpm),
        zone_headers=headers,
        zone_rows=tuple(zip(*columns, strict=True)),
    )


def applied_text(result: ThresholdTestResult, period: ValidityPeriod) -> str:
    """What the status bar says after a test is applied: the threshold, for how long it holds
    and that the load is being recalculated."""
    translate = QCoreApplication.translate
    return " ".join(
        (
            translate("ThresholdTexts", "Threshold test applied: {bpm} bpm.").format(
                bpm=QLocale().toString(result.threshold_bpm)
            ),
            saved_period_text(MeasurementSeries.HEART_RATE, period),
            translate("ThresholdTexts", "The load is being recalculated."),
        )
    )


# --- Why a test is rejected -------------------------------------------------------------------


def rejection_message(
    rejection: ThresholdRejection, protocol: ThresholdProtocol = DEFAULT_PROTOCOL
) -> ErrorMessage:
    """What is wrong with the activity as a test of this protocol, in plain words, and how to go
    on."""
    translate = QCoreApplication.translate
    minutes = QLocale().toString(protocol_minutes(protocol))
    average = QLocale().toString(THRESHOLD_WINDOW_S // _SECONDS_PER_MINUTE)
    seconds = QLocale().toString(MAX_PAUSE_S)
    title = translate("ThresholdTexts", "This activity cannot be used as a threshold test")
    texts = {
        ThresholdRejection.WRONG_SPORT: (
            translate("ThresholdTexts", "The activity is not a run."),
            translate("ThresholdTexts", "Choose a running or trail running activity."),
        ),
        ThresholdRejection.TOO_SHORT: (
            translate("ThresholdTexts", "The activity is shorter than {minutes} minutes.").format(
                minutes=minutes
            ),
            translate(
                "ThresholdTexts",
                "Choose an activity with at least {minutes} minutes of running, or repeat the "
                "test.",
            ).format(minutes=minutes),
        ),
        ThresholdRejection.TOO_MANY_PAUSES: (
            translate(
                "ThresholdTexts",
                "The activity has more than {seconds} seconds of pauses in the {minutes} minutes "
                "of the test.",
            ).format(seconds=seconds, minutes=minutes),
            translate(
                "ThresholdTexts",
                "A test needs a steady effort without stopping. Repeat it without pausing the "
                "watch.",
            ),
        ),
        ThresholdRejection.TOO_FEW_HEART_RATE_SAMPLES: (
            translate(
                "ThresholdTexts",
                "The heart-rate data is missing or has gaps in the last {minutes} minutes of the "
                "test.",
            ).format(minutes=average),
            translate(
                "ThresholdTexts",
                "Check that the heart-rate sensor is worn well, then repeat the test.",
            ),
        ),
        ThresholdRejection.THRESHOLD_OUT_OF_RANGE: (
            translate(
                "ThresholdTexts",
                "The heart rate of the test is not between your minimum and maximum heart rate.",
            ),
            translate(
                "ThresholdTexts",
                "Check the limits in Profile > Edit profile, or repeat the test at a steady "
                "maximum effort.",
            ),
        ),
    }
    what, remedy = texts[rejection]
    return ErrorMessage(title, what, remedy)


# --- When to repeat the test ------------------------------------------------------------------


def retest_text(status: RetestStatus) -> str:
    """Where today stands against the retest window, as one sentence."""
    translate = QCoreApplication.translate
    match status:
        case NoTest():
            return translate("ThresholdTexts", "No test yet")
        case NotYet(first_day=first, last_day=last):
            return translate("ThresholdTexts", "Next test from {first} to {last}").format(
                first=date_text(first), last=date_text(last)
            )
        case Recommended(first_day=first, last_day=last):
            return translate(
                "ThresholdTexts", "A new test is recommended from {first} to {last}"
            ).format(first=date_text(first), last=date_text(last))
        case Overdue(days=late):
            return translate(
                "ThresholdTexts",
                "The last test is %n days old: a new test is recommended",
                "",
                RETEST_MAX_DAYS + late,
            )


def retest_status_bar_text(status: RetestStatus) -> str:
    """The retest sentence with its subject, for the status bar."""
    return QCoreApplication.translate("ThresholdTexts", "Threshold test: {status}").format(
        status=retest_text(status)
    )


# --- The history ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """A row of the history table: texts for the view and keys for sorting."""

    cells: tuple[str, str, str, str, str]
    keys: tuple[float | str, float | str, float | str, float | str, float | str]


def _activity_text(activity: ActivityBrief | None, zone: ZoneInfo) -> str:
    if activity is None:
        return QCoreApplication.translate("ThresholdTexts", "No longer in the profile")
    return QCoreApplication.translate("ThresholdTexts", "{sport}, {started}").format(
        sport=sport_text(activity.sport), started=_date_time_text(activity.start, zone)
    )


def history_row(entry: HistoryEntry, zone: ZoneInfo) -> HistoryRow:
    """The texts and sort keys of one test."""
    test = entry.test
    locale = QLocale()
    activity = _activity_text(entry.activity, zone)
    method = protocol_method_text(test.protocol, test.method_version)
    pace = "" if test.pace_s_per_km is None else pace_text(test.pace_s_per_km)
    pace_key = -1.0 if test.pace_s_per_km is None else float(test.pace_s_per_km)
    return HistoryRow(
        (date_text(test.test_day), activity, locale.toString(test.threshold_bpm), pace, method),
        (
            float(test.test_day.toordinal()),
            activity.casefold(),
            float(test.threshold_bpm),
            pace_key,
            method.casefold(),
        ),
    )
