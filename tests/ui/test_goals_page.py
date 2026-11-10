# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The Goals page without a window around it: the state of the plan in words with a remedy, the
table of goals with its verdicts, numbers on the right, sorting by value."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest
from PySide6.QtCore import QLocale, Qt
from PySide6.QtWidgets import QFrame
from pytestqt.qtbot import QtBot

from ponos_coach.activities.domain import Sport
from ponos_coach.goals.domain import FitnessGoal, GoalId, Priority, RaceGoal
from ponos_coach.goals.ports import Goal
from ponos_coach.planning.domain import (
    MissingLongSession,
    PlanRefusal,
    RefusalReason,
    TargetAssessment,
    TargetClass,
)
from ponos_coach.profiles.rebuild_plan import NotBuiltReason
from ponos_coach.ui.goals_page import GoalsPage
from ponos_coach.ui.goals_table import GOAL_ID_ROLE, SORT_ROLE, GoalColumn
from ponos_coach.ui.plan_presentation import Notice, NoticeAction, Severity
from ponos_coach.ui.plan_texts import label_lookup
from ponos_coach.ui.tokens import Theme, tokens_for, warning_color_for

MARATHON = RaceGoal(GoalId("m"), date(2026, 10, 18), Priority.A, Sport.RUNNING, 42_195, 12_600)
TEN_K = RaceGoal(GoalId("k"), date(2026, 8, 30), Priority.B, Sport.RUNNING, 10_000, None)
FORM = FitnessGoal(GoalId("f"), date(2026, 9, 20), 60.0)
GOALS: list[Goal] = [MARATHON, TEN_K, FORM]


@pytest.fixture(autouse=True)
def english() -> Iterator[None]:
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    yield
    QLocale.setDefault(previous)


def make_page(qtbot: QtBot) -> GoalsPage:
    page = GoalsPage()
    qtbot.addWidget(page)
    return page


def show(
    page: GoalsPage,
    notices: tuple[Notice, ...] = (),
    goals: list[Goal] | None = None,
    assessments: dict[str, TargetAssessment] | None = None,
    theme: Theme = Theme.LIGHT,
) -> None:
    items = GOALS if goals is None else goals
    page.show_state(
        notices,
        items,
        assessments or {},
        label_lookup({g.goal_id.value: g for g in items}),
        theme,
    )


def cells(page: GoalsPage, row: int) -> list[str]:
    return [str(page.proxy.index(row, c).data()) for c in range(len(GoalColumn))]


def test_without_goals_the_page_says_how_to_begin(qtbot: QtBot) -> None:
    page = make_page(qtbot)

    show(page, goals=[])

    assert page.empty_text == "No goals yet. To get a training plan, add your first race."
    assert page.table.isHidden()


def test_with_goals_the_table_replaces_the_empty_state(qtbot: QtBot) -> None:
    page = make_page(qtbot)

    show(page)

    assert page.empty_text == ""
    assert not page.table.isHidden()


def test_goals_are_sorted_by_date_and_show_kind_sport_distance_and_target_time(
    qtbot: QtBot,
) -> None:
    page = make_page(qtbot)

    show(page)

    assert cells(page, 0) == ["8/30/26", "Race B", "Running", "10", "", "", "", ""]
    assert cells(page, 1) == ["9/20/26", "Form goal", "", "", "", "", "", ""]
    assert cells(page, 2) == ["10/18/26", "Race A", "Running", "42.195", "", "3:30:00", "", ""]


def test_numbers_are_aligned_on_the_right_and_texts_on_the_left(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    show(page)
    right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    left = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    def alignment(column: GoalColumn) -> object:
        return page.proxy.index(0, column).data(Qt.ItemDataRole.TextAlignmentRole)

    assert alignment(GoalColumn.DISTANCE) == right
    assert alignment(GoalColumn.TARGET_TIME) == right
    assert alignment(GoalColumn.KIND) == left
    header = page.model.headerData(
        GoalColumn.DISTANCE, Qt.Orientation.Horizontal, Qt.ItemDataRole.TextAlignmentRole
    )
    assert header == right


def test_the_table_has_no_grid_lines_and_a_name_for_the_screen_reader(qtbot: QtBot) -> None:
    page = make_page(qtbot)

    assert not page.table.showGrid()
    assert page.table.accessibleName() == "Goals"
    assert page.table.isSortingEnabled()


def test_sorting_by_distance_or_time_goes_by_value_not_by_text(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    show(page)

    page.table.sortByColumn(GoalColumn.DISTANCE, Qt.SortOrder.DescendingOrder)
    descending = [page.proxy.goal_id_at(row) for row in range(3)]
    page.table.sortByColumn(GoalColumn.TARGET_TIME, Qt.SortOrder.AscendingOrder)
    by_time = [page.proxy.goal_id_at(row) for row in range(3)]

    assert descending == ["m", "k", "f"]  # 42.195 before 10 (as text "10" would come first)
    assert by_time[-1] == "m"  # the only one with a target time is last when ascending
    assert page.model.index(0, GoalColumn.DISTANCE).data(SORT_ROLE) == 42_195.0


def test_the_goal_selected_is_known_by_its_id_and_survives_a_refresh(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    seen: list[object] = []
    page.selection_changed.connect(seen.append)
    show(page)

    page.select_goal("k")
    show(page, goals=[MARATHON, TEN_K])

    assert page.selected_goal_id() == "k"
    assert seen[-1] == "k"
    assert page.proxy.index(0, 0).data(GOAL_ID_ROLE) == "k"


def test_enter_on_a_goal_asks_to_change_it(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    show(page)
    asked: list[str] = []
    page.goal_activated.connect(asked.append)

    page.table.activated.emit(page.proxy.index(2, 0))

    assert asked == ["m"]


@pytest.mark.parametrize(
    ("target_class", "word"),
    [
        (TargetClass.REALISTIC, "Realistic"),
        (TargetClass.AMBITIOUS, "Ambitious"),
        (TargetClass.UNREACHABLE, "Not reachable"),
        (TargetClass.NOT_ESTIMABLE, "Cannot be estimated"),
    ],
)
def test_the_verdict_on_a_target_time_is_a_word_beside_the_race(
    qtbot: QtBot, target_class: TargetClass, word: str
) -> None:
    page = make_page(qtbot)
    best = None if target_class is TargetClass.NOT_ESTIMABLE else 12_000
    assessment = TargetAssessment("m", target_class, best)

    show(page, assessments={"m": assessment})

    verdict = page.proxy.index(2, GoalColumn.VERDICT)
    assert verdict.data() == word
    assert verdict.data(Qt.ItemDataRole.ToolTipRole).startswith(word)


def test_the_tooltip_of_a_verdict_gives_the_best_time_when_there_is_one(qtbot: QtBot) -> None:
    page = make_page(qtbot)

    show(page, assessments={"m": TargetAssessment("m", TargetClass.AMBITIOUS, 12_345)})

    tip = page.proxy.index(2, GoalColumn.VERDICT).data(Qt.ItemDataRole.ToolTipRole)
    assert tip.endswith("Best time possible by the race: 3:25:45")


def test_a_notice_opens_with_a_word_then_the_sentence_and_the_remedy(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    notice = Notice(Severity.WARNING, MissingLongSession("m"), NoticeAction.TRAINING_SETTINGS)

    show(page, (notice,))

    (text,) = page.notice_texts
    assert text.split("\n") == [
        "Warning",
        "No long session is chosen for Race A, Marathon, 10/18/26: your endurance will lag.",
        "Choose the long session in Training settings.",
    ]


def test_a_refusal_is_a_no_plan_notice_and_its_button_asks_for_the_command(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    asked: list[object] = []
    page.notice_action_requested.connect(asked.append)
    refusal = Notice(
        Severity.NO_PLAN, PlanRefusal(RefusalReason.NO_AVAILABILITY), NoticeAction.TRAINING_SETTINGS
    )

    show(page, (refusal,))
    (button,) = page.notice_buttons()
    button.click()

    assert page.notice_texts[0].startswith("No plan\nNo training time is set.")
    assert button.text() == "Training settings..."
    assert asked == [NoticeAction.TRAINING_SETTINGS]


@pytest.mark.parametrize(
    ("action", "label"),
    [
        (NoticeAction.ADD_RACE, "Add race..."),
        (NoticeAction.OPEN_GOALS, "Show the goals"),
        (NoticeAction.EDIT_PROFILE, "Edit profile..."),
    ],
)
def test_each_remedy_has_its_own_button_and_a_notice_without_one_has_none(
    qtbot: QtBot, action: NoticeAction, label: str
) -> None:
    page = make_page(qtbot)
    needs = Notice(Severity.NO_PLAN, NotBuiltReason.MISSING_PHYSIOLOGY, action)
    plain = Notice(Severity.WARNING, MissingLongSession("m"), NoticeAction.NONE)

    show(page, (needs,))
    assert [b.text() for b in page.notice_buttons()] == [label]
    show(page, (plain,))
    assert page.notice_buttons() == []


def test_the_two_levels_have_their_own_border_colour_in_both_themes(qtbot: QtBot) -> None:
    page = make_page(qtbot)
    no_plan = Notice(Severity.NO_PLAN, PlanRefusal(RefusalReason.NO_GOALS), NoticeAction.ADD_RACE)
    warning = Notice(Severity.WARNING, MissingLongSession("m"), NoticeAction.NONE)

    for theme in Theme:
        show(page, (no_plan, warning), theme=theme)
        frames = [f for f in page.findChildren(QFrame, "planNotice") if not f.isHidden()]
        sheets = [frame.styleSheet() for frame in frames]
        assert tokens_for(theme).text_muted in sheets[0]
        assert tokens_for(theme).error not in sheets[0]  # no plan is not an error
        assert warning_color_for(theme) in sheets[1]
    assert warning_color_for(Theme.LIGHT) != warning_color_for(Theme.DARK)


@pytest.mark.parametrize("theme", list(Theme))
def test_the_colours_of_the_two_levels_reach_three_to_one_on_the_background(theme: Theme) -> None:
    background = tokens_for(theme).background

    for colour in (tokens_for(theme).text_muted, warning_color_for(theme)):
        assert _contrast(colour, background) >= 3.0


def _luminance(color: str) -> float:
    def channel(value: int) -> float:
        fraction = value / 255
        return fraction / 12.92 if fraction <= 0.03928 else ((fraction + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(int(color[i : i + 2], 16)) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)
