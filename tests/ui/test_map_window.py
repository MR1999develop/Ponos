# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-MAP-02, T-MAP-04, T-MAP-06, T-FILT-04: the map beside the table, in the real window.

Eight made-up runs, all starting at one invented point in the open sea. The map is driven through
the controller (the view the page would report).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.load.activity_profile import ActivityProfileData
from ponos_coach.ui import map_query_host
from ponos_coach.ui.activity_filters import FilterTexts
from ponos_coach.ui.main_window import MainWindow
from tests.ui.map_support import (
    ACTIVITIES_WITH_POSITION,
    ELSEWHERE,
    FAR,
    NEAR,
    map_window,
    show_view,
)
from tests.ui.workspace_support import WAIT_MS, make_settings


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


def listed(window: MainWindow) -> int:
    """How many activities the map says start in the visible area (0 while it says nothing)."""
    count = window.map_page.summary_texts[0]
    return int(count.rsplit(": ", 1)[1]) if ": " in count else 0


def shown_in_table(window: MainWindow) -> int:
    return window.activities_page.proxy.rowCount()


def wait_search(qtbot: QtBot, window: MainWindow, count: int) -> None:
    qtbot.waitUntil(
        lambda: (
            window.map_ui.controller.search is not None
            and len(window.map_ui.controller.search.activities) == count
        ),
        timeout=WAIT_MS,
    )


def test_the_map_is_under_the_table_and_counts_its_area(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, NEAR)

    right = window.panes.right_column
    top = right.widget(0)
    assert top is not None
    assert top.isAncestorOf(window.activities_page)
    assert right.widget(1) is window.map_page
    assert listed(window) == ACTIVITIES_WITH_POSITION
    count, totals, extra = window.map_page.summary_texts
    assert count == f"Activities in this area: {ACTIVITIES_WITH_POSITION}"
    assert " km " in totals
    assert "ascent" in totals
    assert extra == ""


def test_an_area_without_activities_says_so_and_how_to_find_them(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, ELSEWHERE)

    assert listed(window) == 0
    count, totals, extra = window.map_page.summary_texts
    assert count == "Activities in this area: 0"
    assert totals == ""
    assert "No activity in this area" in extra


def test_zoomed_out_the_list_still_counts_the_starts_and_tells_why_there_are_no_tracks(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)

    show_view(qtbot, window, FAR)

    assert listed(window) == ACTIVITIES_WITH_POSITION
    assert "Select an activity to see its track" in window.map_page.summary_texts[2]


def test_the_list_follows_the_shared_filter_and_so_does_the_table(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    activities = window.activities_page.model.rowCount()

    window.map_ui.filters.apply(FilterTexts(date_from="2026-05-04"))

    wait_search(qtbot, window, 5)  # runs of 4 to 8 May (day 3 to 7 after the first)
    qtbot.waitUntil(lambda: shown_in_table(window) == 5, timeout=WAIT_MS)
    qtbot.waitUntil(lambda: listed(window) == 5, timeout=WAIT_MS)
    assert window.activities_page.model.rowCount() == activities  # the model keeps every row
    assert window.activities_page.count_text == f"5 of {activities} activities"


def test_selecting_in_the_table_selects_on_the_map_and_the_other_way_round(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller

    window.activities_page.table.selectRow(2)
    chosen = window.activities_page.selected_activity()
    assert chosen is not None
    assert controller.selected == chosen.identity

    # A click on the map (a position of the search result) selects the row of the table too.
    controller.select_index(0)
    other = controller.selected
    assert other is not None
    assert other != chosen.identity
    now_selected = window.activities_page.selected_activity()
    assert now_selected is not None
    assert now_selected.identity == other


def test_the_table_can_be_narrowed_to_the_visible_area_and_the_choice_is_remembered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    activities = window.activities_page.model.rowCount()
    check = window.activities_page.area_only_check
    assert check.isVisibleTo(window.activities_page)
    assert not check.isChecked()
    assert shown_in_table(window) == activities

    check.setChecked(True)
    show_view(qtbot, window, ELSEWHERE)  # nothing starts there

    qtbot.waitUntil(lambda: shown_in_table(window) == 0, timeout=WAIT_MS)
    assert window.activities_page.model.rowCount() == activities
    show_view(qtbot, window, NEAR)
    qtbot.waitUntil(lambda: shown_in_table(window) == activities, timeout=WAIT_MS)
    check.setChecked(False)
    show_view(qtbot, window, ELSEWHERE)
    assert shown_in_table(window) == activities  # unchecked: the table ignores the area
    check.setChecked(True)
    assert bool(settings.value("activities/visibleAreaOnly", False, type=bool))


def test_the_page_receives_the_selected_activity_by_position_in_the_search(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    search = controller.search
    assert search is not None

    controller.select_index(3)

    assert controller.selected == search.activities[3].identity
    assert controller.index_of(search.activities[3].identity) == 3


def test_the_json_for_the_page_is_built_on_the_data_thread_not_on_the_gui_thread(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    threads: dict[str, set[int]] = {"starts": set(), "tracks": set(), "profile": set()}

    def recording(kind: str, original: Callable[..., object]) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            threads[kind].add(threading.get_ident())
            return original(*args, **kwargs)

        return wrapper

    for kind, name in (
        ("starts", "starts_message"),
        ("tracks", "tracks_message"),
        ("profile", "profile_message"),
    ):
        monkeypatch.setattr(map_query_host, name, recording(kind, getattr(map_query_host, name)))
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller

    controller.select_index(0)
    qtbot.waitUntil(lambda: controller.profile_script is not None, timeout=WAIT_MS)

    gui = threading.get_ident()
    for kind, seen in threads.items():
        assert seen, kind  # it was built...
        assert gui not in seen, kind  # ...and not here
    assert controller.starts_script is not None
    assert controller.tracks_script is not None


def test_a_click_that_names_an_older_list_is_dropped(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    bridge = window.map_page.map_view.bridge
    current = controller.list_generation
    assert bridge.activity_generation == current  # the page was given the number of the list
    assert controller.starts_script is not None
    assert f'"gen":{current}' in controller.starts_script
    clicked: list[int] = []
    bridge.activity_clicked.connect(clicked.append)

    bridge.activityClicked(2, current - 1)  # the page answers about the list before this one
    bridge.activityClicked(2, current)

    assert clicked == [2]
    assert bridge.stale == 1


def test_a_new_search_gets_a_new_generation(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    controller = window.map_ui.controller
    before = controller.list_generation

    controller.refresh_search()
    qtbot.waitUntil(lambda: controller.list_generation != before, timeout=WAIT_MS)

    assert controller.list_generation > before
    assert window.map_page.map_view.bridge.activity_generation == controller.list_generation


def test_a_selection_outside_the_list_changes_nothing(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    controller = window.map_ui.controller

    for wrong in (-1, 8, 10**9, True, "3", 2.5, None):
        controller.select_index(wrong)

    assert controller.selected is None


def test_the_selected_activity_shows_its_profile_below_the_map(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    profiles: list[object] = []
    window.map_ui.controller.profile_changed.connect(profiles.append)

    window.map_ui.controller.select_index(0)

    qtbot.waitUntil(
        lambda: any(isinstance(p, ActivityProfileData) for p in profiles), timeout=WAIT_MS
    )
    profile = next(p for p in profiles if isinstance(p, ActivityProfileData))
    assert len(profile) > 10
    assert profile.altitude_m is not None
    title, _ = window.map_page.detail_texts
    assert "Running" in title
    assert window.map_page.shows_chart
    # beside the profile, the activity's data (its detail window opens from the list or Ctrl+D)
    assert window.map_page.summary_text("moving").count(":") == 2
    assert window.map_page.summary_text("elapsed").count(":") == 2


def test_an_activity_removed_by_the_filter_is_no_longer_selected(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    controller.select_index(0)  # the oldest run
    assert controller.selected is not None

    window.map_ui.filters.apply(FilterTexts(sports=frozenset({Sport.CYCLING})))

    wait_search(qtbot, window, 0)
    assert controller.selected is None


# --- Active calories beside the profile --------------------------------------------------------


def test_the_data_beside_the_profile_shows_the_estimated_active_calories(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)

    assert window.map_page.summary_text("calories") == ""  # nothing selected
    window.map_ui.controller.select_index(0)

    qtbot.waitUntil(lambda: window.map_page.summary_text("calories") != "", timeout=WAIT_MS)
    assert window.map_page.summary_text("calories").startswith("≈ ")  # the runs declare none


def test_without_the_body_data_the_calories_stay_empty(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    monkeypatch.setattr(map_query_host.MapQueryHost, "energy", lambda *_args: None)
    arrived: list[object] = []
    window.map_ui.controller.energy_changed.connect(lambda _identity, e: arrived.append(e))

    window.map_ui.controller.select_index(0)

    qtbot.waitUntil(lambda: bool(arrived), timeout=WAIT_MS)
    assert arrived == [None]
    assert window.map_page.summary_text("calories") == ""


def test_after_a_quick_change_of_selection_only_the_last_activity_gets_its_calories(
    qtbot: QtBot, tmp_path: Path, settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = map_window(qtbot, tmp_path, settings)
    show_view(qtbot, window, NEAR)
    controller = window.map_ui.controller
    gate = threading.Event()
    calls: list[object] = []
    real = map_query_host.MapQueryHost.energy

    def slow_for_the_first(self: object, access: object, identity: object) -> object:
        calls.append(identity)
        if len(calls) == 1:
            gate.wait(timeout=5)  # the first answer arrives after the selection changed
        return real(self, access, identity)  # type: ignore[arg-type]

    monkeypatch.setattr(map_query_host.MapQueryHost, "energy", slow_for_the_first)
    arrived: list[object] = []
    controller.energy_changed.connect(lambda identity, _e: arrived.append(identity))

    controller.select_index(0)
    first = controller.selected
    controller.select_index(1)
    second = controller.selected
    gate.set()

    qtbot.waitUntil(lambda: bool(arrived), timeout=WAIT_MS)
    qtbot.wait(300)  # the stale answer of the first, if it were not dropped, would be here
    assert first != second
    assert arrived == [second]
