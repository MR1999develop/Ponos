# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""From the Garmin JSON listing to domain types, hostile entries included (T-SEC-03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ponos_coach.activities.domain import MAX_PAGE_SIZE, GarminActivityId, RemoteActivity, Sport
from ponos_coach.garmin.listing import (
    MAX_TOTAL_COUNT,
    UnexpectedListingError,
    parse_entry,
    parse_page,
    parse_total,
)
from tests.fixtures.garmin import load_json
from tests.garmin.scripted import entry_for

VALID: dict[str, object] = {
    "activityId": 77,
    "startTimeGMT": "2026-05-06 07:00:00",
    "duration": 1800.0,
    "activityType": {"typeKey": "running"},
}


def entry(**changes: object) -> dict[str, object]:
    return {**VALID, **changes}


class TestRecordedLookingPage:
    def test_the_valid_page_gives_five_activities_newest_first_as_listed(self) -> None:
        items, raw_count = parse_page(load_json("activity_list_page.json"))

        assert raw_count == 5
        assert [item.garmin_id.value for item in items] == [
            1000000005,
            1000000004,
            1000000003,
            1000000002,
            1000000001,
        ]

    def test_first_entry_values(self) -> None:
        items, _ = parse_page(load_json("activity_list_page.json"))

        first = items[0]
        assert first.start == datetime(2026, 5, 6, 7, 0, tzinfo=UTC)
        assert first.duration_s == 1800.4  # elapsedDuration, not duration
        assert first.sport_key == "running"
        assert first.sport is Sport.RUNNING
        assert first.has_heart_rate is True

    def test_real_shaped_entries_have_a_numeric_heart_rate_and_elapsed_duration(self) -> None:
        items, _ = parse_page(load_json("activity_list_page.json"))

        assert [item.has_heart_rate for item in items] == [True] * 5
        assert [item.duration_s for item in items] == [1800.4, 3720.0, 2460.0, 3000.0, 1530.0]

    def test_string_id_and_decimal_time_are_read(self) -> None:
        item = parse_entry(entry(activityId="1000004", startTimeGMT="2026-05-05 07:00:00.0"))

        assert item is not None
        assert item.garmin_id == GarminActivityId(1000004)
        assert item.start == datetime(2026, 5, 5, 7, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("changes", "expected"),
        [({"averageHR": None}, False), ({"averageHR": 0}, False), ({"averageHR": 148.0}, True)],
    )
    def test_heart_rate_null_and_zero_mean_not_recorded(
        self, changes: dict[str, object], expected: bool
    ) -> None:
        item = parse_entry(entry(**changes))

        assert item is not None
        assert item.has_heart_rate is expected

    def test_a_missing_heart_rate_key_is_unknown(self) -> None:
        item = parse_entry(VALID)

        assert item is not None
        assert item.has_heart_rate is None

    def test_duration_is_the_fallback_when_elapsed_duration_is_absent(self) -> None:
        item = parse_entry(entry(duration=2400.0))

        assert item is not None
        assert item.duration_s == 2400.0

    def test_elapsed_duration_wins_over_duration(self) -> None:
        item = parse_entry(entry(duration=2400.0, elapsedDuration=3000.0))

        assert item is not None
        assert item.duration_s == 3000.0

    def test_the_count_response_is_a_bare_integer(self) -> None:
        assert parse_total(load_json("count_activities.json")) == 1234

    def test_a_dict_with_an_activity_list_is_accepted(self) -> None:
        items, raw_count = parse_page({"activityList": [VALID]})

        assert (len(items), raw_count) == (1, 1)


class TestHostileEntries:
    def test_only_the_two_valid_entries_of_the_hostile_page_survive(self) -> None:
        raw = load_json("activity_list_hostile.json")
        assert isinstance(raw, list)

        items, raw_count = parse_page(raw)

        assert raw_count == len(raw)
        assert [item.garmin_id.value for item in items] == [2000001, 2000027]

    def test_html_and_sql_in_names_are_never_read(self) -> None:
        items, _ = parse_page(load_json("activity_list_hostile.json"))

        assert all("script" not in repr(item) and "DROP" not in repr(item) for item in items)

    def test_a_repeated_id_keeps_the_first_entry_and_counts_both(self) -> None:
        first, second = entry(startTimeGMT="2026-05-06 07:00:00"), entry(duration=60.0)

        items, raw_count = parse_page([first, second])

        assert raw_count == 2
        assert [item.duration_s for item in items] == [1800.0]

    @pytest.mark.parametrize(
        "changes",
        [
            {"activityId": "../1"},
            {"activityId": "\uff11\uff12\uff13"},
            {"activityId": 2**63},
            {"activityId": None},
            {"startTimeGMT": "2026-05-06"},
            {"startTimeGMT": "2026-02-30 07:00:00"},
            {"startTimeGMT": "2026-05-06 07:00:00" + "0" * 10_000},
            {"startTimeGMT": "1980-01-01 00:00:00"},
            {"duration": True},
            {"duration": float("inf")},
            {"duration": float("nan")},
            {"activityType": {"typeKey": "a" * 65}},
            {"activityType": ["running"]},
        ],
    )
    def test_malformed_values_discard_the_entry(self, changes: dict[str, object]) -> None:
        assert parse_entry(entry(**changes)) is None

    @pytest.mark.parametrize("missing", ["activityId", "startTimeGMT", "duration", "activityType"])
    def test_a_missing_key_discards_the_entry(self, missing: str) -> None:
        broken = {key: value for key, value in VALID.items() if key != missing}

        assert parse_entry(broken) is None

    def test_a_duration_that_ends_after_the_window_is_discarded(self) -> None:
        late = entry(startTimeGMT="2099-12-31 23:00:00", duration=7200.0)

        assert parse_entry(late) is None

    def test_deeply_nested_extra_data_is_never_walked(self) -> None:
        nested: object = []
        for _ in range(200_000):
            nested = [nested]

        item = parse_entry(entry(details=nested, activityName="x" * 1_000_000))

        assert item is not None
        assert item.garmin_id.value == 77

    def test_heart_rate_that_is_not_a_number_is_unknown(self) -> None:
        item = parse_entry(entry(averageHR="alta"))

        assert item is not None
        assert item.has_heart_rate is None

    def test_time_with_a_fraction_is_truncated_to_the_millisecond(self) -> None:
        item = parse_entry(entry(startTimeGMT="2026-05-06T07:00:00.123456Z"))

        assert item is not None
        assert item.start == datetime(2026, 5, 6, 7, 0, 0, 123000, tzinfo=UTC)


class TestPageShape:
    @pytest.mark.parametrize("raw", [None, 5, "testo", {"altro": []}, {"activityList": 3}, True])
    def test_a_response_that_is_not_a_list_is_unexpected(self, raw: object) -> None:
        with pytest.raises(UnexpectedListingError):
            parse_page(raw)

    def test_more_than_the_maximum_page_size_is_unexpected(self) -> None:
        with pytest.raises(UnexpectedListingError):
            parse_page([VALID] * (MAX_PAGE_SIZE + 1))

    def test_exactly_the_maximum_page_size_is_accepted(self) -> None:
        _, raw_count = parse_page([VALID] * MAX_PAGE_SIZE)

        assert raw_count == MAX_PAGE_SIZE

    def test_an_empty_list_is_an_empty_page(self) -> None:
        assert parse_page([]) == ((), 0)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(0, 0), (5, 5), (MAX_TOTAL_COUNT, MAX_TOTAL_COUNT)],
    )
    def test_a_sensible_total_is_kept(self, raw: object, expected: int) -> None:
        assert parse_total(raw) == expected

    @pytest.mark.parametrize("raw", [-1, MAX_TOTAL_COUNT + 1, 1.5, "5", None, True, [5]])
    def test_a_strange_total_is_unknown(self, raw: object) -> None:
        assert parse_total(raw) is None


_JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=12), children, max_size=4)
    ),
    max_leaves=25,
)
_EXACT_ENTRY_KEYS = st.sampled_from(
    ["activityId", "startTimeGMT", "duration", "elapsedDuration", "activityType", "averageHR"]
)


class TestInvariants:
    @given(st.lists(_JSON_VALUES, max_size=15))
    def test_any_list_gives_distinct_valid_items_no_more_than_received(
        self, values: list[object]
    ) -> None:
        items, raw_count = parse_page(values)

        assert raw_count == len(values)
        assert len(items) <= raw_count
        assert len({item.garmin_id for item in items}) == len(items)

    @given(st.dictionaries(_EXACT_ENTRY_KEYS, _JSON_VALUES, max_size=6))
    def test_an_entry_with_arbitrary_values_is_valid_or_discarded_never_an_error(
        self, mixed: dict[str, object]
    ) -> None:
        item = parse_entry(entry(**mixed))

        assert item is None or isinstance(item, RemoteActivity)

    @given(
        st.integers(min_value=1, max_value=2**63 - 1),
        st.datetimes(
            min_value=datetime(1991, 1, 1), max_value=datetime(2090, 1, 1), timezones=st.just(UTC)
        ),
        st.floats(min_value=1.0, max_value=1e6),
        st.from_regex(r"[a-z][a-z0-9_]{0,63}", fullmatch=True),
        st.sampled_from([True, False, None]),
    )
    def test_what_the_builder_writes_the_parser_reads_back(
        self,
        number: int,
        start: datetime,
        duration_s: float,
        sport_key: str,
        has_heart_rate: bool | None,
    ) -> None:
        start = start.replace(microsecond=start.microsecond // 1000 * 1000)
        activity = RemoteActivity(
            GarminActivityId(number), start, duration_s, sport_key, has_heart_rate
        )

        assert parse_entry(entry_for(activity)) == activity

    @given(st.datetimes(min_value=datetime(1991, 1, 1), max_value=datetime(2090, 1, 1)))
    def test_times_have_no_offset_dependency(self, naive: datetime) -> None:
        text = f"{naive:%Y-%m-%d %H:%M:%S}"
        item = parse_entry(entry(startTimeGMT=text))

        assert item is not None
        assert item.start == naive.replace(microsecond=0, tzinfo=UTC)
        assert item.start.utcoffset() == timedelta(0)
