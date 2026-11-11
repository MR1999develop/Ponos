# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The calendar with the plan: future days that open, planned against done, goals marked with their
priority and the phases of the macrocycle (never by colour alone). Plans come from the real engine;
the athlete is made up."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QWidget
from pytestqt.qtbot import QtBot

from ponos_coach.assessment.domain import ThresholdProtocol
from ponos_coach.goals.domain import GoalId, SessionType
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import DayAdherence, Phase, Plan, PlanDay
from ponos_coach.planning.ports import FrozenDay
from ponos_coach.ui.calendar_model import (
    CalendarDay,
    PlanLayer,
    build_month,
    first_of_month,
    grid_range,
    last_month_shown,
    plan_layer_of,
)
from ponos_coach.ui.calendar_page import STRIPE_ROLE, CalendarPage, phase_color
from ponos_coach.ui.calendar_texts import cell_description, cell_text, day_detail_text
from ponos_coach.ui.plan_texts import phase_text
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import profile_activity
from tests.ui.plan_support import MARATHON_DAY, PLAN_TODAY, marathon, plan_for

TARGET_S = 3 * 3600 + 20 * 60


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


@pytest.fixture(scope="module")
def plan() -> Plan:
    return plan_for(marathon(target_s=TARGET_S))


def session_day(plan: Plan, after: date | None = None) -> PlanDay:
    """The first day of the plan with a structured session (blocks), after a day."""
    return next(
        d
        for d in plan.days
        if d.session is not None and d.session.blocks and (after is None or d.day > after)
    )


def cell_for(plan: Plan, day: date, today: date, goals: list[Goal] | None = None) -> CalendarDay:
    layer = plan_layer_of(plan, [], [], goals or [])
    month = build_month(first_of_month(day), today, [], [], layer)
    return next(c for c in month.days if c.day == day)


def detail_of_selected(page: CalendarPage) -> str:
    """The tooltip of the selected cell: the day in full."""
    text = page.model.data(page.table.currentIndex(), Qt.ItemDataRole.ToolTipRole)
    assert isinstance(text, str)
    return text


def test_a_day_after_today_can_be_selected_and_carries_its_planned_day(plan: Plan) -> None:
    target = session_day(plan)

    cell = cell_for(plan, target.day, PLAN_TODAY)

    assert cell.is_future
    assert cell.can_select
    assert cell.can_open  # a day of the plan holds a page: an estimate from the plan
    assert cell.plan == target
    assert cell.adherence is None


def test_days_outside_the_month_carry_no_plan(plan: Plan) -> None:
    layer = plan_layer_of(plan, [], [], [marathon()])
    month = build_month(date(2026, 3, 1), PLAN_TODAY, [], [], layer)

    outside = [c for c in month.days if not c.in_month]

    assert outside
    assert all(c.plan is None and c.goals == () and not c.can_select for c in outside)


def test_a_day_frozen_in_the_past_wins_over_the_day_of_the_current_plan(plan: Plan) -> None:
    current = plan.days[3]
    was = PlanDay(current.day, Phase.RECOVERY, None, None, 0.0)

    layer = plan_layer_of(plan, [FrozenDay(current.day - timedelta(days=10), was)], [], [])

    assert layer.planned[current.day] == was
    assert layer.planned[plan.days[4].day] == plan.days[4]


def test_a_goal_is_on_its_day_with_the_verdict_on_its_target_time(plan: Plan) -> None:
    race = marathon(target_s=TARGET_S)

    cell = cell_for(plan, MARATHON_DAY, PLAN_TODAY, [race])

    (item,) = cell.goals
    assert item.goal == race
    assert item.assessment is not None
    assert item.assessment.goal_id == "marathon"


def test_a_future_cell_names_the_session_the_phase_and_the_race_with_its_priority(
    plan: Plan,
) -> None:
    target = session_day(plan)
    race_cell = cell_for(plan, MARATHON_DAY, PLAN_TODAY, [marathon()])

    lines = cell_text(cell_for(plan, target.day, PLAN_TODAY)).split("\n")
    race_lines = cell_text(race_cell).split("\n")

    assert lines[0] == str(target.day.day)
    assert re.fullmatch(r"Plan: .+, \d+ min", lines[1])
    assert lines[2] == phase_text(target.phase)
    assert "Race A" in race_lines
    assert phase_text(Phase.RACE) in race_lines


def test_the_day_of_the_threshold_test_is_named_so_in_the_cell_and_in_the_detail(
    plan: Plan,
) -> None:
    target = session_day(plan)
    assert target.session is not None
    test = dataclasses.replace(
        target.session,
        session_type=SessionType.THRESHOLD,
        race_pace=False,
        threshold_test=ThresholdProtocol.THIRTY_MINUTES,
    )
    days = tuple(
        dataclasses.replace(d, session=test) if d.day == target.day else d for d in plan.days
    )
    with_test = dataclasses.replace(plan, days=days)

    cell = cell_for(with_test, target.day, PLAN_TODAY)

    assert f"Threshold test, {test.minutes} min" in cell_text(cell)
    assert f"Threshold test, {test.minutes} min" in day_detail_text(cell)


def test_the_phase_is_in_the_text_of_every_planned_cell_not_only_in_a_colour(plan: Plan) -> None:
    month_firsts = {first_of_month(d.day) for d in plan.days}

    for first in month_firsts:
        for cell in build_month(first, PLAN_TODAY, [], [], plan_layer_of(plan, [], [], [])).days:
            if cell.plan is not None:
                assert phase_text(cell.plan.phase) in cell_text(cell).split("\n")


def test_a_rest_day_is_not_written_in_the_grid_but_the_tooltip_and_the_detail_say_so() -> None:
    rest = PlanDay(date(2026, 3, 4), Phase.BASE, None, None, 0.0)
    layer = PlanLayer(planned={rest.day: rest})
    cell = next(
        c
        for c in build_month(date(2026, 3, 1), PLAN_TODAY, [], [], layer).days
        if c.day == rest.day
    )

    assert cell_text(cell).split("\n") == ["4", phase_text(Phase.BASE)]
    assert "Planned: rest day" in cell_description(cell)
    assert "Rest day" in day_detail_text(cell)


def test_the_detail_of_a_future_day_lists_the_blocks_zones_heart_rate_and_load(plan: Plan) -> None:
    target = session_day(plan)
    assert target.session is not None

    text = day_detail_text(cell_for(plan, target.day, PLAN_TODAY))

    sections = text.split("\n\n")
    assert sections[1].split("\n")[0] == phase_text(target.phase)
    for block in target.session.blocks:
        assert f"zone {block.zone}" in text
        assert f"{block.hr_low_bpm} to {block.hr_high_bpm} bpm" in text
    assert "min" in sections[1]
    assert re.search(r"Estimated load: \d+ TRIMPeq", text)


def test_the_detail_of_a_race_day_gives_the_expected_time_and_the_verdict(plan: Plan) -> None:
    text = day_detail_text(cell_for(plan, MARATHON_DAY, PLAN_TODAY, [marathon(target_s=TARGET_S)]))

    assert "Race A, Marathon" in text
    assert re.search(r"The plan expects \d+:\d\d:\d\d", text)
    assert re.search(r"Target time: (Realistic|Ambitious|Not reachable)", text)


def test_a_past_day_shows_what_was_planned_beside_what_was_done_and_what_was_missed(
    plan: Plan,
) -> None:
    planned = plan.days[5]
    today = planned.day + timedelta(days=2)
    start = datetime(planned.day.year, planned.day.month, planned.day.day, 8, tzinfo=UTC)
    done = profile_activity(start, load=40.0)
    frozen = FrozenDay(planned.day - timedelta(days=9), planned)
    adherence = DayAdherence(planned.day, 100.0, 40.0)
    layer = plan_layer_of(None, [frozen], [adherence], [])
    month = build_month(first_of_month(planned.day), today, [done], [], layer)
    cell = next(c for c in month.days if c.day == planned.day)

    text = day_detail_text(cell)

    assert cell.can_open
    assert cell.plan == planned
    assert cell.adherence == adherence
    assert "Done: Running, 40 TRIMPeq" in text
    assert "Planned 100 TRIMPeq, done 40 TRIMPeq" in text
    assert "Not done: 60 TRIMPeq" in text
    assert text.index("Estimated load") < text.index("Done:")  # planned first, then done


def test_extra_work_on_a_day_does_not_show_as_missed(plan: Plan) -> None:
    planned = plan.days[5]
    adherence = DayAdherence(planned.day, 50.0, 80.0)
    cell = next(
        c
        for c in build_month(
            first_of_month(planned.day),
            planned.day + timedelta(days=1),
            [],
            [],
            plan_layer_of(
                None, [FrozenDay(planned.day - timedelta(days=3), planned)], [adherence], []
            ),
        ).days
        if c.day == planned.day
    )

    assert "Not done" not in day_detail_text(cell)


# --- The page --------------------------------------------------------------------------------


@pytest.fixture
def page(qtbot: QtBot, plan: Plan) -> CalendarPage:
    widget = CalendarPage(PLAN_TODAY)
    qtbot.addWidget(widget)
    widget.set_plan_layer(plan_layer_of(plan, [], [], [marathon(target_s=TARGET_S)]))
    return widget


def test_a_future_planned_day_opens_in_the_panel_with_its_session(
    page: CalendarPage, plan: Plan
) -> None:
    target = session_day(plan)
    page.go_today()
    while page.current_month < first_of_month(target.day):
        page.next_month()

    page.select_day(target.day)

    assert page.selected_day() == target.day
    detail = detail_of_selected(page)
    assert phase_text(target.phase) in detail
    assert "Estimated load" in detail
    assert "Warm-up" in detail or "Steady" in detail


def test_selecting_a_day_tells_who_follows_the_selection(page: CalendarPage) -> None:
    seen: list[object] = []
    page.selection_changed.connect(seen.append)

    page.select_day(PLAN_TODAY)
    QTest.keyClick(page.table, Qt.Key.Key_Right)

    assert seen[-2:] == [PLAN_TODAY, PLAN_TODAY + timedelta(days=1)]


def test_the_selection_survives_a_new_plan(page: CalendarPage, plan: Plan) -> None:
    page.select_day(PLAN_TODAY + timedelta(days=3))

    page.set_plan_layer(plan_layer_of(plan, [], [], []))

    assert page.selected_day() == PLAN_TODAY + timedelta(days=3)


def test_the_stripe_of_a_cell_is_the_colour_of_its_phase_and_follows_the_theme(
    page: CalendarPage, plan: Plan
) -> None:
    day = plan.days[2]
    index = page.model.index_of(day.day)

    light = page.model.data(index, STRIPE_ROLE)
    page.apply_theme(Theme.DARK)
    dark = page.model.data(index, STRIPE_ROLE)

    assert light == phase_color(day.phase, Theme.LIGHT)
    assert dark == phase_color(day.phase, Theme.DARK)
    assert page.model.data(page.model.index_of(date(2026, 2, 3)), STRIPE_ROLE) is None


@pytest.mark.parametrize("theme", list(Theme))
def test_phase_colours_are_not_warm_and_stand_out_from_the_background(theme: Theme) -> None:
    background = tokens_for(theme).background
    colours = {phase: phase_color(phase, theme) for phase in Phase}

    assert len(set(colours.values())) >= 6  # taper and mini taper share one; names tell them apart
    for colour in colours.values():
        assert _contrast(colour, background) >= 3.0


def test_the_phase_legend_names_only_the_phases_of_the_month_shown(
    page: CalendarPage, plan: Plan
) -> None:
    month = build_month(page.current_month, PLAN_TODAY, [], [], plan_layer_of(plan, [], [], []))
    expected = tuple(
        phase_text(phase)
        for phase in Phase
        if phase in {c.plan.phase for c in month.days if c.plan is not None}
    )

    assert page.phase_legend_texts == expected
    assert expected


def test_without_a_plan_there_is_no_phase_legend(qtbot: QtBot) -> None:
    widget = CalendarPage(PLAN_TODAY)
    qtbot.addWidget(widget)

    assert widget.phase_legend_texts == ()


def test_the_calendar_goes_a_year_ahead_and_not_further(page: CalendarPage) -> None:
    for _ in range(30):
        page.next_month()

    assert page.current_month == last_month_shown(PLAN_TODAY)
    assert page.current_month == date(2027, 3, 1)
    assert not page.next_button.isEnabled()


def test_the_visible_range_is_the_whole_weeks_of_the_grid(page: CalendarPage) -> None:
    assert page.visible_range == grid_range(page.current_month)
    first, last = page.visible_range
    assert first.weekday() == 0
    assert last.weekday() == 6


# --- Under the grid only the legends; the day in full in the tooltip of its cell ---------------


def test_under_the_grid_there_is_no_day_panel(page: CalendarPage) -> None:
    page.select_day(PLAN_TODAY + timedelta(days=9))

    assert page.findChild(QWidget, "calendarDayPanel") is None
    buttons = [
        b for b in page.findChildren(QPushButton) if b is not page.plan_alert_strip.button
    ]  # the strip over the grid is hidden until a goal is out of reach
    assert [b.text() for b in buttons] == ["Previous month", "Next month"]


def test_the_tooltip_of_a_day_lists_its_goals(page: CalendarPage) -> None:
    page.go_today()
    while page.current_month < first_of_month(MARATHON_DAY):
        page.next_month()

    page.select_day(MARATHON_DAY)

    assert "Race A, Marathon" in detail_of_selected(page)


def test_no_selection_says_how_to_start(page: CalendarPage) -> None:
    page.table.clearSelection()
    page.table.setCurrentIndex(page.model.index(0, 0))  # a cell outside the month: not selectable

    assert page.selected_day() is None


def test_another_goal_id_in_a_cell_does_not_leak_into_the_next_day(
    page: CalendarPage, plan: Plan
) -> None:
    other = marathon("second", PLAN_TODAY + timedelta(days=3))
    page.set_plan_layer(plan_layer_of(plan, [], [], [other]))
    page.select_day(other.day)
    assert "Race A" in detail_of_selected(page)

    page.select_day(other.day + timedelta(days=1))

    assert "Race A" not in detail_of_selected(page)
    assert GoalId("second").value not in detail_of_selected(page)


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        fraction = value / 255
        return fraction / 12.92 if fraction <= 0.03928 else ((fraction + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(int(color[i : i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_the_stripe_of_the_phase_is_really_painted_on_the_top_edge_of_the_cell(
    qtbot: QtBot, page: CalendarPage, plan: Plan
) -> None:
    day = plan.days[2]
    page.resize(1000, 700)
    page.show()
    qtbot.waitExposed(page)
    index = page.model.index_of(day.day)
    rect = page.table.visualRect(index)

    image = page.table.viewport().grab().toImage()

    top = image.pixelColor(rect.x() + rect.width() // 2, rect.y() + 1)
    below = image.pixelColor(rect.x() + rect.width() // 2, rect.y() + 12)
    assert top.name().upper() == phase_color(day.phase, Theme.LIGHT).upper()
    assert below.name().upper() != top.name().upper()
