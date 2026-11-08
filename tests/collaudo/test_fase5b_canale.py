# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
# ruff: noqa: RUF001
"""Phase 5b acceptance campaign, the page channel: T-SEC-14 and T-SEC-15 for what the map page
sends to Python and what Python sends to the page.

The page is ours but it is still web content: every message is parsed or dropped (counted), and
what goes back is plain JSON through a closed set of functions.
"""

from __future__ import annotations

import json
import math
import random

import pytest

from ponos_coach.ui.map_messages import MAX_MAP_ZOOM, PAGE_FUNCTIONS, page_call, parse_view
from ponos_coach.ui.map_web_view import MapBridge

HOSTILE_NUMBERS = [
    math.nan, math.inf, -math.inf, 1e308, -1e308, 0.0, -0.0, 5e-324, 2**63, -(2**63), 2**200,
]  # fmt: skip


def bridge_with_counts(activities: int = 8, points: int = 100) -> MapBridge:
    bridge = MapBridge()
    bridge.activity_count = activities
    bridge.profile_points = points
    return bridge


@pytest.mark.parametrize("value", HOSTILE_NUMBERS)
def test_t_sec_14_a_hostile_view_is_dropped_and_counted(value: float) -> None:
    bridge = bridge_with_counts()
    seen: list[object] = []
    bridge.view_changed.connect(seen.append)

    for position in range(4):
        edges = [0.0, 0.0, 1.0, 1.0]
        edges[position] = value
        bridge.viewChanged(edges[0], edges[1], edges[2], edges[3], 5)

    assert bridge.rejected + len(seen) == 4
    for view in seen:  # whatever got through is a plausible, finite view
        assert 0 <= view.zoom <= MAX_MAP_ZOOM  # type: ignore[attr-defined]
        box = view.box  # type: ignore[attr-defined]
        assert all(math.isfinite(v) for v in (box.south, box.west, box.north, box.east))


@pytest.mark.parametrize("zoom", [-1, 20, 2**31, -(2**31), 2**70])
def test_t_sec_14_a_zoom_outside_the_map_is_dropped(zoom: int) -> None:
    bridge = bridge_with_counts()
    seen: list[object] = []
    bridge.view_changed.connect(seen.append)

    bridge.viewChanged(0.0, 0.0, 1.0, 1.0, zoom)

    assert seen == []
    assert bridge.rejected == 1


@pytest.mark.parametrize("position", [-1, 8, 9, 2**31, -(2**31), 2**70, 10**30])
def test_t_sec_14_an_activity_outside_the_list_python_sent_is_dropped(position: int) -> None:
    bridge = bridge_with_counts(activities=8)
    clicked: list[int] = []
    hovered: list[int] = []
    bridge.activity_clicked.connect(clicked.append)
    bridge.point_hovered.connect(hovered.append)

    bridge.activityClicked(position)
    bridge.pointHovered(position)

    assert clicked == [] or position < 8
    assert bridge.rejected >= 1


def test_t_sec_14_with_nothing_sent_every_click_and_hover_is_dropped() -> None:
    bridge = bridge_with_counts(activities=0, points=0)
    seen: list[int] = []
    bridge.activity_clicked.connect(seen.append)
    bridge.point_hovered.connect(seen.append)

    for value in (0, 1, -1):
        bridge.activityClicked(value)
        bridge.pointHovered(value)

    assert seen == []
    assert bridge.rejected == 6


def test_t_sec_14_the_boundaries_of_the_list_are_exact() -> None:
    bridge = bridge_with_counts(activities=8)
    clicked: list[int] = []
    bridge.activity_clicked.connect(clicked.append)

    for position in (-1, 0, 7, 8):
        bridge.activityClicked(position)

    assert clicked == [0, 7]


def test_t_sec_14_random_garbage_never_raises_and_every_message_is_accounted_for() -> None:
    rng = random.Random(3)
    bridge = bridge_with_counts()
    accepted: list[object] = []
    bridge.view_changed.connect(accepted.append)
    bridge.activity_clicked.connect(accepted.append)
    bridge.point_hovered.connect(accepted.append)
    sent = 0
    for _ in range(2000):
        pool = HOSTILE_NUMBERS + [rng.uniform(-400, 400) for _ in range(5)]
        south, west, north, east = (rng.choice(pool) for _ in range(4))
        bridge.viewChanged(south, west, north, east, rng.randrange(-5, 30))
        bridge.activityClicked(rng.randrange(-20, 30))
        bridge.pointHovered(rng.randrange(-20, 200))
        sent += 3

    assert len(accepted) + bridge.rejected == sent


def test_t_sec_14_parse_view_rejects_non_numbers_of_every_shape() -> None:
    junk: list[object] = ["1", None, [], {}, b"1", object(), 1j, True, False]
    for value in junk:
        for slot in range(4):
            args: list[object] = [0.0, 0.0, 1.0, 1.0]
            args[slot] = value
            result = parse_view(args[0], args[1], args[2], args[3], 5)
            if isinstance(value, bool):
                assert result is None  # a bool is not a coordinate
            else:
                assert result is None


# --- Python to the page: a closed set of functions, JSON only ----------------------------------


@pytest.mark.parametrize(
    "name",
    ["eval", "alert", "init(); alert(1); //", "setStarts;alert(1)", "", "ponosMap", "__proto__",
     "setStarts\n", "SETSTARTS", "setStarts ", "constructor"],
)  # fmt: skip
def test_t_sec_14_only_the_known_page_functions_can_be_called(name: str) -> None:
    with pytest.raises(ValueError, match="not a page function"):
        page_call(name, {})


@pytest.mark.parametrize("name", sorted(PAGE_FUNCTIONS))
def test_t_sec_14_every_allowed_call_is_one_statement_with_a_json_argument(name: str) -> None:
    hostile = {
        "a": "</script><script>alert(1)</script>",
        "b": "   ' \" \\ \x00 😀 é",
        "c": [1, 2.5, None, True],
    }

    script = page_call(name, hostile)

    assert script.isascii()
    assert "\n" not in script and " " not in script
    assert script.startswith(f"ponosMap.{name}(") and script.endswith(");")
    assert json.loads(script[len(f"ponosMap.{name}(") : -2]) == hostile


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_t_sec_14_non_finite_numbers_never_reach_the_page(bad: float) -> None:
    with pytest.raises(ValueError, match="Out of range float values"):
        page_call("setView", {"lat": bad})
