# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Activity filters (T-FILT-01...03) and area search (T-MAP-02, 03, 04, 07)."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import (
    ActivityFilter,
    ActivityRecord,
    ActivitySummary,
    FilterInputError,
    FilterInputReason,
    GeoBox,
    GeoPoint,
    NumericRange,
    Sport,
    StartIndex,
    activities_in_area,
    encode_segments,
    filter_activities,
    parse_amount,
    parse_distance_km,
    parse_duration,
    track_touches_area,
)
from tests.builders import fit_identity, make_record

DAY = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)


def record(
    number: int = 1,
    *,
    day: datetime = DAY,
    timer_s: float = 3600.0,
    distance_m: float | None = 10_000.0,
    ascent_m: float | None = 100.0,
    sport: Sport = Sport.RUNNING,
    start: tuple[float, float] | None = None,
) -> ActivityRecord:
    point = None if start is None else GeoPoint(*start)
    return make_record(
        fit_identity(number),
        start=day,
        elapsed_s=timer_s,
        sport=sport,
        summary=ActivitySummary(distance_m=distance_m, ascent_m=ascent_m, start_point=point),
    )


# --- T-FILT-01: each filter on its own ----------------------------------------------------------


def test_empty_filter_lets_everything_through_even_unknown_values() -> None:
    records = [record(1), record(2, distance_m=None, ascent_m=None), record(3, sport=Sport.OTHER)]

    assert ActivityFilter().is_empty
    assert filter_activities(records, ActivityFilter()) == records


@pytest.mark.parametrize(
    ("criteria", "inside", "outside"),
    [
        (
            ActivityFilter(date_from=date(2026, 5, 10), date_to=date(2026, 5, 10)),
            [
                DAY,
                datetime(2026, 5, 10, 0, 0, tzinfo=UTC),
                datetime(2026, 5, 10, 23, 59, tzinfo=UTC),
            ],
            [datetime(2026, 5, 9, 23, 59, tzinfo=UTC), datetime(2026, 5, 11, 0, 0, tzinfo=UTC)],
        ),
        (
            ActivityFilter(date_from=date(2026, 5, 10)),
            [DAY, datetime(2030, 1, 1, tzinfo=UTC)],
            [datetime(2026, 5, 9, 23, 59, tzinfo=UTC)],
        ),
        (
            ActivityFilter(date_to=date(2026, 5, 10)),
            [DAY, datetime(2000, 1, 1, tzinfo=UTC)],
            [datetime(2026, 5, 11, 0, 0, tzinfo=UTC)],
        ),
    ],
)
def test_date_limits_are_whole_days_and_inclusive(
    criteria: ActivityFilter, inside: list[datetime], outside: list[datetime]
) -> None:
    assert all(criteria.matches(record(day=d)) for d in inside)
    assert not any(criteria.matches(record(day=d)) for d in outside)


def test_the_day_is_taken_in_the_given_time_zone() -> None:
    late = datetime(2026, 5, 10, 23, 30, tzinfo=UTC)  # in Rome it is already 01:30 on the 11th
    rome = timezone(timedelta(hours=2))
    criteria = ActivityFilter(date_to=date(2026, 5, 10))

    assert criteria.matches(record(day=late))
    assert not criteria.matches(record(day=late), rome)


@pytest.mark.parametrize(
    ("make_filter", "make_record", "low", "high"),
    [
        (lambda r: ActivityFilter(duration_s=r), lambda v: record(timer_s=v), 1800.0, 3600.0),
        (lambda r: ActivityFilter(distance_m=r), lambda v: record(distance_m=v), 5000.0, 10_000.0),
        (lambda r: ActivityFilter(ascent_m=r), lambda v: record(ascent_m=v), 50.0, 100.0),
    ],
    ids=["duration", "distance", "ascent"],
)
def test_numeric_limits_are_inclusive_and_just_outside_fails(
    make_filter: Callable[[NumericRange], ActivityFilter],
    make_record: Callable[[float], ActivityRecord],
    low: float,
    high: float,
) -> None:
    criteria = make_filter(NumericRange(low, high))

    assert criteria.matches(make_record(low))
    assert criteria.matches(make_record(high))
    assert not criteria.matches(make_record(math.nextafter(low, 0.0)))
    assert not criteria.matches(make_record(math.nextafter(high, math.inf)))


def test_half_open_ranges() -> None:
    assert NumericRange(minimum=10.0).accepts(10.0)
    assert not NumericRange(minimum=10.0).accepts(9.99)
    assert NumericRange(maximum=10.0).accepts(0.0)
    assert not NumericRange(maximum=10.0).accepts(10.01)


def test_unknown_distance_fails_only_a_filter_that_limits_it() -> None:
    indoor = record(distance_m=None, ascent_m=None)

    assert ActivityFilter(duration_s=NumericRange(0.0, 7200.0)).matches(indoor)
    assert not ActivityFilter(distance_m=NumericRange(0.0, None)).matches(indoor)
    assert not ActivityFilter(ascent_m=NumericRange(None, 500.0)).matches(indoor)


def test_sport_filter_empty_means_all_and_several_are_alternatives() -> None:
    run, bike, hike = (
        record(sport=Sport.RUNNING),
        record(sport=Sport.CYCLING),
        record(sport=Sport.HIKING),
    )
    criteria = ActivityFilter(sports=frozenset({Sport.RUNNING, Sport.HIKING}))

    assert [criteria.matches(r) for r in (run, bike, hike)] == [True, False, True]


def test_inverted_limits_are_swapped() -> None:
    assert NumericRange.of(10.0, 5.0) == NumericRange(5.0, 10.0)
    assert NumericRange.of(None, 5.0) == NumericRange(None, 5.0)
    swapped = ActivityFilter(date_from=date(2026, 6, 1), date_to=date(2026, 5, 1))
    assert (swapped.date_from, swapped.date_to) == (date(2026, 5, 1), date(2026, 6, 1))


@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, 2e9])
def test_invalid_range_limits_are_rejected(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        NumericRange(bad, None)


def test_direct_construction_with_inverted_limits_is_an_error() -> None:
    with pytest.raises(ValueError, match="exceeds"):
        NumericRange(10.0, 5.0)


# --- T-FILT-02: combined filters against a naive filter -------------------------------------

bounds = st.one_of(st.none(), st.floats(0.0, 50_000.0))
days = st.dates(date(2026, 1, 1), date(2026, 3, 31))


def naive_match(r: ActivityRecord, c: ActivityFilter) -> bool:  # noqa: PLR0911
    """Filter written out in full, without `accepts` or `is_open`."""
    if c.date_from is not None and r.start.date() < c.date_from:
        return False
    if c.date_to is not None and r.start.date() > c.date_to:
        return False
    if c.sports and r.sport not in c.sports:
        return False
    for rng, value in (
        (c.duration_s, r.timer_s),
        (c.distance_m, r.summary.distance_m),
        (c.ascent_m, r.summary.ascent_m),
    ):
        if rng.minimum is None and rng.maximum is None:
            continue
        if value is None:
            return False
        if rng.minimum is not None and value < rng.minimum:
            return False
        if rng.maximum is not None and value > rng.maximum:
            return False
    return True


@st.composite
def filters(draw: st.DrawFn) -> ActivityFilter:
    return ActivityFilter(
        date_from=draw(st.none() | days),
        date_to=draw(st.none() | days),
        duration_s=NumericRange.of(draw(bounds), draw(bounds)),
        distance_m=NumericRange.of(draw(bounds), draw(bounds)),
        ascent_m=NumericRange.of(draw(bounds), draw(bounds)),
        sports=frozenset(draw(st.lists(st.sampled_from(Sport), max_size=3))),
    )


@st.composite
def records(draw: st.DrawFn) -> ActivityRecord:
    return record(
        draw(st.integers(1, 10**6)),
        day=datetime.combine(draw(days), datetime.min.time(), tzinfo=UTC)
        + timedelta(hours=draw(st.integers(0, 23))),
        timer_s=draw(st.floats(1.0, 50_000.0)),
        distance_m=draw(st.none() | st.floats(0.0, 50_000.0)),
        ascent_m=draw(st.none() | st.floats(0.0, 5000.0)),
        sport=draw(st.sampled_from(Sport)),
    )


@given(filters(), st.lists(records(), max_size=30))
def test_t_filt_02_combined_filters_agree_with_a_naive_filter(
    criteria: ActivityFilter, items: list[ActivityRecord]
) -> None:
    assert filter_activities(items, criteria) == [r for r in items if naive_match(r, criteria)]


# --- T-FILT-03: tolerant input ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("90", 5400.0),
        ("1:30", 5400.0),
        ("01:30", 5400.0),
        ("0:45", 2700.0),
        ("1:30:15", 5415.0),
        ("  90  ", 5400.0),
        ("90,5", 5430.0),
        ("90.5", 5430.0),
        ("0", 0.0),
    ],
)
def test_duration_texts(text: str, seconds: float) -> None:
    assert parse_duration(text) == seconds


@pytest.mark.parametrize(
    ("text", "meters"),
    [
        ("10", 10_000.0),
        ("10,5", 10_500.0),
        ("10.5", 10_500.0),
        ("0,001", 1.0),
        ("", None),
        ("  ", None),
    ],
)
def test_distance_texts_in_kilometres(text: str, meters: float | None) -> None:
    assert parse_distance_km(text) == meters


def test_comma_and_point_are_the_same_number() -> None:
    assert parse_amount("1234,5") == parse_amount("1234.5") == 1234.5


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("-5", FilterInputReason.NEGATIVE),
        (chr(0x2212) + "5", FilterInputReason.NEGATIVE),  # typographic minus sign
        ("abc", FilterInputReason.NOT_A_NUMBER),
        ("1,2,3", FilterInputReason.NOT_A_NUMBER),
        ("1e5", FilterInputReason.NOT_A_NUMBER),
        ("nan", FilterInputReason.NOT_A_NUMBER),
        ("inf", FilterInputReason.NOT_A_NUMBER),
        ("1:75", FilterInputReason.NOT_A_NUMBER),  # minutes above 59
        ("1:", FilterInputReason.NOT_A_NUMBER),
        ("9" * 40, FilterInputReason.TOO_LARGE),
        ("9" * 12, FilterInputReason.NOT_A_NUMBER),  # more digits than the syntax allows
    ],
)
def test_invalid_duration_and_amount_texts_are_rejected_with_a_reason(
    text: str, reason: FilterInputReason
) -> None:
    with pytest.raises(FilterInputError) as caught:
        parse_duration(text)
    assert caught.value.reason is reason
    assert text not in str(caught.value)  # the input is not repeated in the message


def test_huge_values_are_too_large() -> None:
    with pytest.raises(FilterInputError) as caught:
        parse_distance_km("999999999")
    assert caught.value.reason is FilterInputReason.TOO_LARGE


@given(st.text(max_size=60))
def test_parsers_only_raise_filter_input_errors(text: str) -> None:
    for parse in (parse_duration, parse_distance_km, parse_amount):
        try:
            value = parse(text)
        except FilterInputError:
            continue
        assert value is None or 0.0 <= value <= 1_000_000_000.0


# --- T-MAP-02, 03, 04, 07: activities in the area ---------------------------------------

AREA = GeoBox(40.0, 8.0, 46.0, 12.0)


def test_only_activities_starting_in_the_area_are_listed_with_their_totals() -> None:
    inside_a = record(1, start=(43.0, 10.0), distance_m=10_000.0, ascent_m=100.0, timer_s=3600.0)
    inside_b = record(
        2, start=(40.0, 12.0), distance_m=5_500.0, ascent_m=40.0, timer_s=1800.0
    )  # edge
    outside = record(3, start=(50.0, 10.0))
    indoor = record(4, start=None, distance_m=None, ascent_m=None)

    result = activities_in_area([inside_a, outside, inside_b, indoor], AREA)

    assert result.activities == (inside_a, inside_b)  # input order
    assert result.totals.count == 2
    assert result.totals.distance_km == pytest.approx(15.5)
    assert result.totals.ascent_m == pytest.approx(140.0)
    assert result.totals.moving_hours == pytest.approx(1.5)
    assert result.without_position == 1


def test_t_map_04_activities_without_position_are_counted_apart_for_every_area() -> None:
    items = [record(1, start=(43.0, 10.0)), record(2), record(3)]
    index = StartIndex(items)

    assert index.in_area(AREA).without_position == 2
    assert index.in_area(GeoBox(-90.0, -180.0, 90.0, 180.0)).without_position == 2
    assert index.in_area(GeoBox(-10.0, 0.0, -9.0, 1.0)).activities == ()


def test_t_map_03_no_activities_and_empty_area() -> None:
    result = activities_in_area([], AREA)

    assert result.activities == ()
    assert result.totals.count == 0
    assert result.totals.distance_km == 0.0
    assert result.without_position == 0


def test_t_map_03_area_across_the_antimeridian() -> None:
    east = record(1, start=(-17.0, 179.9))
    west = record(2, start=(-17.0, -179.9))
    far = record(3, start=(-17.0, 0.0))
    on_the_line = record(4, start=(-17.0, 180.0))
    area = GeoBox.from_view(-20.0, 170.0, -10.0, 190.0)

    result = activities_in_area([east, west, far, on_the_line], area)

    assert result.activities == (east, west, on_the_line)


def test_t_map_03_many_activities_at_the_same_point() -> None:
    items = [record(n, start=(43.0, 10.0)) for n in range(1, 501)]

    result = activities_in_area(items, GeoBox(43.0, 10.0, 43.0, 10.0))  # one-point area

    assert result.totals.count == 500


def test_t_map_03_extreme_zoom_boxes() -> None:
    near = record(1, start=(43.000001, 10.000001))
    index = StartIndex([near])

    assert index.in_area(GeoBox(43.0, 10.0, 43.00001, 10.00001)).totals.count == 1
    assert index.in_area(GeoBox(43.1, 10.0, 43.2, 10.1)).totals.count == 0
    assert index.in_area(GeoBox.from_view(-1000.0, -1000.0, 1000.0, 1000.0)).totals.count == 1


area_boxes = st.builds(
    lambda south, height, west, width: GeoBox.from_view(
        south, west, min(south + height, 90.0), west + width
    ),
    st.floats(-90.0, 90.0),
    st.floats(0.0, 180.0),
    st.floats(-180.0, 180.0),
    st.floats(0.0, 400.0),
)
starts = st.none() | st.tuples(st.floats(-90.0, 90.0), st.floats(-180.0, 180.0))


def naive_inside(point: tuple[float, float], area: GeoBox) -> bool:
    """Containment written out on a 360-degree circle, without `contains_many`."""
    lat, lon = point
    if not area.south <= lat <= area.north:
        return False
    if area.west == -180.0 and area.east == 180.0:
        return True
    span = (area.east - area.west) % 360.0
    # the same meridian has three names on the unrolled circle
    return any(area.west <= lon + turn <= area.west + span for turn in (-360.0, 0.0, 360.0))


@given(area_boxes, st.lists(starts, max_size=40))
def test_t_map_02_list_equals_the_naive_filtering_of_the_start_points(
    area: GeoBox, positions: list[tuple[float, float] | None]
) -> None:
    items = [record(n + 1, start=p) for n, p in enumerate(positions)]

    result = activities_in_area(items, area)

    expected = [
        r for r, p in zip(items, positions, strict=True) if p is not None and naive_inside(p, area)
    ]
    assert list(result.activities) == expected
    assert result.totals.count == len(expected)
    assert result.without_position == positions.count(None)


@given(st.lists(starts, max_size=30), st.lists(starts, max_size=30))
def test_t_map_07_filter_then_area_equals_area_then_filter(
    first: list[tuple[float, float] | None], second: list[tuple[float, float] | None]
) -> None:
    items = [
        record(n + 1, start=p, timer_s=1000.0 * (n % 5 + 1)) for n, p in enumerate(first + second)
    ]
    criteria = ActivityFilter(duration_s=NumericRange(2000.0, 4000.0))

    one = activities_in_area(filter_activities(items, criteria), AREA)
    other = [r for r in activities_in_area(items, AREA).activities if criteria.matches(r)]

    assert list(one.activities) == other


def test_track_touching_an_area_is_decided_on_the_track_points() -> None:
    track = encode_segments([[GeoPoint(30.0, 5.0), GeoPoint(43.0, 10.0)], [GeoPoint(60.0, 60.0)]])

    assert track_touches_area(track, AREA)
    assert not track_touches_area(track, GeoBox(-10.0, -10.0, 0.0, 0.0))
    with pytest.raises(ValueError, match=r"."):
        track_touches_area("\x01", AREA)


def test_the_area_mask_is_vectorised_and_matches_the_scalar_test() -> None:
    lat = np.array([43.0, 50.0, 40.0])
    lon = np.array([10.0, 10.0, 12.0])

    assert AREA.contains_many(lat, lon).tolist() == [True, False, True]
    assert [AREA.contains(a, b) for a, b in zip(lat, lon, strict=True)] == [True, False, True]
