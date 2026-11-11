# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-14 (pure part) and the payloads of the map page: validation and shape, no window."""

from __future__ import annotations

import json
import math

import numpy as np
import pytest

from ponos_coach.activities.domain import ActivityRecord, AreaTotals, GeoPoint, Sport, identity_key
from ponos_coach.activities.map_queries import MapTrack, SearchResult
from ponos_coach.ui.map_messages import (
    index_by_identity,
    page_call,
    parse_activity_index,
    parse_view,
    starts_message,
    tracks_message,
)
from ponos_coach.ui.map_style import SPORT_ORDER, sport_index
from tests.builders import fit_identity, make_record

VALID = (-30.1, -20.1, -29.9, -19.9, 12)


def test_a_plausible_view_becomes_a_box_and_a_zoom() -> None:
    view = parse_view(*VALID)

    assert view is not None
    assert view.zoom == 12
    assert (view.box.south, view.box.west, view.box.north, view.box.east) == (
        -30.1,
        -20.1,
        -29.9,
        -19.9,
    )


def test_a_view_beyond_the_globe_is_brought_back_into_it() -> None:
    view = parse_view(-95.0, 170.0, 95.0, 190.0, 3)  # a map shows copies of the world

    assert view is not None
    assert (view.box.south, view.box.north) == (-90.0, 90.0)
    assert view.box.crosses_antimeridian  # west 170 .. east -170


@pytest.mark.parametrize(
    "bad",
    [
        (math.nan, 0, 1, 1, 5),
        (0, math.inf, 1, 1, 5),
        (0, 0, 1, -math.inf, 5),
        (0, 0, 1, 1, 21),  # zoom above the map's range (20: CyclOSM)
        (0, 0, 1, 1, -1),
        (0, 0, 1, 1, 5.5),  # a zoom is an integer
        (0, 0, 1, 1, True),
        (0, 0, 1, 1, "5"),
        ("0", 0, 1, 1, 5),
        (None, 0, 1, 1, 5),
        (0, 0, 1, 1e308, 5),  # a longitude no map shows
        (0, 10, 1, 5, 5),  # east before west
    ],
)
def test_an_implausible_view_is_rejected(bad: tuple[object, ...]) -> None:
    assert parse_view(*bad) is None


@pytest.mark.parametrize(
    ("value", "count", "expected"),
    [
        (0, 8, 0),
        (7, 8, 7),
        (8, 8, None),  # one past the end
        (-1, 8, None),
        (10**12, 8, None),
        (0, 0, None),  # nothing was sent
        (True, 8, None),
        (3.0, 8, None),
        ("3", 8, None),
        (None, 8, None),
    ],
)
def test_the_page_names_an_activity_only_by_a_position_in_what_python_sent(
    value: object, count: int, expected: int | None
) -> None:
    assert parse_activity_index(value, count) == expected


def test_only_the_closed_set_of_page_functions_can_be_called() -> None:
    assert page_call("setStarts", {"data": []}) == 'ponosMap.setStarts({"data":[]});'
    for name in ("alert", "eval", "init(0);alert", "setStarts ", "SetStarts", ""):
        with pytest.raises(ValueError, match="page function"):
            page_call(name, {})


def test_a_payload_with_text_stays_a_json_string_literal() -> None:
    hostile = "');alert(1);//" + chr(0x2028) + "</script>"

    script = page_call("setTheme", {"text": hostile})

    body = script[len("ponosMap.setTheme(") : -2]
    assert json.loads(body) == {"text": hostile}  # one literal: nothing escapes into code
    assert script.isascii()  # no raw line separators either


def record(number: int, sport: Sport = Sport.RUNNING) -> ActivityRecord:
    return make_record(fit_identity(number), sport=sport)


def search_of(records: list[ActivityRecord]) -> SearchResult:
    located = np.arange(len(records), dtype=np.intp)
    return SearchResult(
        tuple(records),
        AreaTotals(len(records), 0.0, 0.0, 0.0),
        0,
        located,
        np.full(len(records), -30.123456789),
        np.full(len(records), -20.987654321),
    )


def test_starts_carry_the_position_in_the_list_and_the_sport_index_not_names() -> None:
    result = search_of([record(0), record(1, Sport.CYCLING)])

    data = starts_message(result)["data"]

    assert isinstance(data, list)
    assert data == [
        -30.12346,
        -20.98765,
        0,
        sport_index(Sport.RUNNING),
        -30.12346,
        -20.98765,
        1,
        sport_index(Sport.CYCLING),
    ]
    json.dumps(data, allow_nan=False)  # finite numbers only


def test_tracks_are_keyed_by_position_and_leave_out_activities_not_in_the_list() -> None:
    result = search_of([record(0), record(1)])
    inside = MapTrack(
        result.activities[1].identity,
        Sport.RUNNING,
        ((GeoPoint(-30.0, -20.0), GeoPoint(-29.9, -20.0)),),
    )
    outside = MapTrack(
        record(5).identity, Sport.RUNNING, ((GeoPoint(0.0, 0.0), GeoPoint(1.0, 1.0)),)
    )

    message = tracks_message([inside, outside], index_by_identity(result), 7)

    assert message == {
        "gen": 7,
        "tracks": [
            {
                "idx": 1,
                "sport": sport_index(Sport.RUNNING),
                "segments": [[-30.0, -20.0, -29.9, -20.0]],
            }
        ],
    }
    assert identity_key(result.activities[0].identity) in index_by_identity(result)


def test_every_sport_has_an_index() -> None:
    assert sorted(sport_index(s) for s in SPORT_ORDER) == list(range(len(Sport)))
