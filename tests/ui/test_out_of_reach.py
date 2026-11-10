# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""A goal whose fitness target is out of reach is told where the user looks: the strip over the
calendar, the goal's day in the calendar, its row in the goals table, and the notice with the
right remedy (the hours, or the ramp)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QLocale, Qt
from pytestqt.qtbot import QtBot

from ponos_coach.planning.domain import (
    CtlTargetNotReached,
    LimitedByHours,
    LimitedByRamp,
    Plan,
    PlanWarning,
)
from ponos_coach.profiles.rebuild_plan import PlanBuilt
from ponos_coach.ui.calendar_model import build_month, first_of_month, plan_layer_of
from ponos_coach.ui.calendar_page import CalendarPage
from ponos_coach.ui.calendar_texts import cell_description, cell_text, day_detail_text
from ponos_coach.ui.goals_page import GoalsPage
from ponos_coach.ui.goals_table import GoalColumn
from ponos_coach.ui.plan_presentation import (
    NoticeAction,
    PlanAlert,
    PlanAvailable,
    notices_of,
    out_of_reach_of,
    plan_alert_of,
)
from ponos_coach.ui.plan_texts import label_lookup, notice_texts
from ponos_coach.ui.tokens import Theme
from tests.ui.goals_support import RACE_DAY, add_race, race_ids, ready_window, wait_for, wait_plan
from tests.ui.goals_support import set_training as set_training_hours
from tests.ui.plan_support import marathon, plan_for
from tests.ui.workspace_support import make_settings

HOURS = LimitedByHours(minutes_needed=540, current_minutes=390)
RAMP = LimitedByRamp()


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def missed(goal_id: str, limit: LimitedByHours | LimitedByRamp = HOURS) -> CtlTargetNotReached:
    return CtlTargetNotReached(goal_id, 41.0, 55.0, limit)


def state_with(*warnings: PlanWarning) -> PlanAvailable:
    return PlanAvailable(replace(plan_for(marathon()), warnings=tuple(warnings)))


def label_of(goal_id: str) -> str:
    return f"goal {goal_id}"


# --- The words and the command of the notice ----------------------------------
def test_limited_by_hours_says_the_hours_needed_and_the_ones_set_and_opens_the_settings() -> None:
    (notice,) = notices_of(state_with(missed("a")))

    headline, remedy = notice_texts(notice, label_of)

    assert (
        headline == "The fitness asked for goal a is out of reach: the plan reaches 41 of 55 CTL."
    )
    assert remedy == (
        "Allow about 9 h a week in the training settings (now 6.5 h), or ask for less fitness "
        "or move the goal later."
    )
    assert notice.action is NoticeAction.TRAINING_SETTINGS


def test_limited_by_the_ramp_keeps_the_old_remedy_and_opens_the_goals() -> None:
    (notice,) = notices_of(state_with(missed("a", RAMP)))

    _headline, remedy = notice_texts(notice, label_of)

    assert remedy == (
        "Move the goal later or ask for less fitness: the plan never ramps faster than the "
        "safe limit."
    )
    assert notice.action is NoticeAction.OPEN_GOALS


# --- What the strip says ----------------------------------
def test_no_alert_without_a_missed_target() -> None:
    assert plan_alert_of(state_with()) is None


def test_the_alert_counts_the_goals_and_asks_for_the_hours_only_when_all_are_the_hours() -> None:
    both_hours = plan_alert_of(state_with(missed("a"), missed("b")))
    mixed = plan_alert_of(state_with(missed("a"), missed("b", RAMP)))

    assert both_hours == PlanAlert(2, all_by_hours=True)
    assert mixed == PlanAlert(2, all_by_hours=False)
    assert both_hours is not None
    assert mixed is not None
    assert both_hours.action is NoticeAction.TRAINING_SETTINGS
    assert mixed.action is NoticeAction.OPEN_GOALS


def make_calendar(qtbot: QtBot) -> CalendarPage:
    page = CalendarPage(plan_for(marathon()).generated_on)
    qtbot.addWidget(page)
    page.show()
    return page


def test_the_strip_appears_with_the_alert_and_goes_away_with_it(qtbot: QtBot) -> None:
    page = make_calendar(qtbot)
    strip = page.plan_alert_strip
    assert strip.isHidden()

    page.set_plan_alert(PlanAlert(2, all_by_hours=True))
    assert not strip.isHidden()
    assert strip.text == "Goals out of reach with the plan's hours: 2"
    assert strip.button.text() == "Training settings..."

    page.set_plan_alert(PlanAlert(1, all_by_hours=False))
    assert strip.text == "Goals out of reach: 1"
    assert strip.button.text() == "Show the goals"

    page.set_plan_alert(None)
    assert strip.isHidden()
    assert strip.text == ""


def test_the_button_of_the_strip_asks_for_the_command(qtbot: QtBot) -> None:
    page = make_calendar(qtbot)
    asked: list[object] = []
    page.plan_alert_action_requested.connect(asked.append)

    page.set_plan_alert(PlanAlert(1, all_by_hours=True))
    page.plan_alert_strip.button.click()
    page.set_plan_alert(PlanAlert(1, all_by_hours=False))
    page.plan_alert_strip.button.click()

    assert asked == [NoticeAction.TRAINING_SETTINGS, NoticeAction.OPEN_GOALS]


# --- The goal's day in the calendar ----------------------------------
def test_the_day_of_a_goal_out_of_reach_has_a_mark_in_words_and_the_remedy_in_the_tooltip() -> None:
    goal = marathon("m")
    plan = replace(plan_for(goal), warnings=(missed("m"),))
    layer = plan_layer_of(plan, [], [], [goal])
    month = build_month(first_of_month(goal.day), plan.generated_on, [], [], layer)
    cell = next(c for c in month.days if c.day == goal.day)

    assert "! Out of reach" in cell_text(cell).split("\n")
    for text in (day_detail_text(cell), cell_description(cell)):
        assert "out of reach: the plan reaches 41 of 55 CTL" in text
        assert "Allow about 9 h a week in the training settings (now 6.5 h)" in text


def test_a_goal_within_reach_has_no_mark() -> None:
    goal = marathon("m")
    plan = plan_for(goal)
    layer = plan_layer_of(plan, [], [], [goal])
    month = build_month(first_of_month(goal.day), plan.generated_on, [], [], layer)
    cell = next(c for c in month.days if c.day == goal.day)

    assert "Out of reach" not in cell_text(cell)
    assert "out of reach" not in day_detail_text(cell)


# --- The row of the goals table ----------------------------------
def test_the_row_of_a_goal_out_of_reach_says_so_and_the_tooltip_has_the_remedy(
    qtbot: QtBot,
) -> None:
    goal = marathon("m")
    page = GoalsPage()
    qtbot.addWidget(page)

    page.show_state((), [goal], {}, label_lookup({"m": goal}), Theme.LIGHT, {"m": missed("m")})

    index = page.proxy.index(0, GoalColumn.FITNESS)
    assert index.data() == "Out of reach: 41 of 55 CTL"
    tooltip = index.data(Qt.ItemDataRole.ToolTipRole)
    assert tooltip.startswith("Allow about 9 h a week")

    page.show_state((), [goal], {}, label_lookup({"m": goal}), Theme.LIGHT)
    assert page.proxy.index(0, GoalColumn.FITNESS).data() == ""


# --- In the window ----------------------------------
def test_the_window_shows_the_strip_while_the_plan_has_the_warning_and_the_button_opens_the_page(
    qtbot: QtBot, tmp_path: Path
) -> None:
    window = ready_window(qtbot, tmp_path, make_settings(tmp_path))
    set_training_hours(qtbot, window)
    add_race(qtbot, window, RACE_DAY)
    plan: Plan = wait_plan(qtbot, window, race_ids(window))
    goal_id = next(iter(race_ids(window)))
    strip = window.calendar_page.plan_alert_strip

    window.controller.plan_rebuilt.emit(
        PlanBuilt(replace(plan, warnings=(missed(goal_id),)), saved=False)
    )

    wait_for(qtbot, lambda: not strip.isHidden())
    assert strip.text == "Goals out of reach with the plan's hours: 1"
    assert out_of_reach_of(window.plan.state).keys() == {goal_id}
    strip.button.click()
    wait_for(qtbot, window.settings_window.isVisible)

    window.controller.plan_rebuilt.emit(PlanBuilt(replace(plan, warnings=()), saved=False))
    wait_for(qtbot, strip.isHidden)
