# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-04 (projection): the future part of the PMC is told apart from the real one, in the legend
and in the line style, never by colour alone, and the races are markers."""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np
import pytest

from ponos_coach.goals.domain import Priority
from ponos_coach.load.domain import compute_pmc
from ponos_coach.planning.domain import PmcPoint
from ponos_coach.ui.chart_payload import render_script
from ponos_coach.ui.pmc_figure import (
    PmcProjection,
    ProjectedRace,
    build_pmc_figure,
    prepare_chart,
)
from ponos_coach.ui.tokens import Theme, tokens_for
from tests.ui.helpers import CHROME, FIRST_DAY, LABELS, small_history

LAST_REAL = FIRST_DAY + timedelta(days=9)


def projection(days: int = 30) -> PmcProjection:
    points = tuple(
        PmcPoint(LAST_REAL + timedelta(days=i), 20.0 + i, 25.0 + i / 2, -5.0 + i / 2)
        for i in range(1, days + 1)
    )
    race = ProjectedRace(LAST_REAL + timedelta(days=20), Priority.A, "Marathon")
    return PmcProjection(points, (race,))


def test_without_a_projection_the_figure_is_the_one_there_always_was() -> None:
    pmc, entries = small_history()
    tokens = tokens_for(Theme.LIGHT)

    plain = build_pmc_figure(pmc, entries, LABELS, tokens)
    explicit = build_pmc_figure(pmc, entries, LABELS, tokens, None)
    empty = build_pmc_figure(pmc, entries, LABELS, tokens, PmcProjection(()))

    assert [t.name for t in plain.data] == [t.name for t in explicit.data]
    assert [t.name for t in plain.data] == [t.name for t in empty.data]
    assert len(plain.data) == 4


def test_the_projection_adds_three_dotted_lines_and_the_races_after_the_real_series() -> None:
    pmc, entries = small_history()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), projection())

    names = [t.name for t in figure.data]
    assert names == [
        "Fitness (CTL)",
        "Fatigue (ATL)",
        "Form (TSB)",
        "Daily load",
        "Fitness (CTL), plan",
        "Fatigue (ATL), plan",
        "Form (TSB), plan",
        "Races",
    ]
    dashes = {t.name: t.line.dash for t in figure.data if t.type == "scatter" and t.line.dash}
    assert dashes["Fitness (CTL), plan"] == "dot"
    assert dashes["Fatigue (ATL), plan"] == "dot"
    assert dashes["Form (TSB), plan"] == "dot"
    assert dashes["Fatigue (ATL)"] == "dash"  # the real ATL keeps its own dashes


def test_the_projected_lines_start_from_the_last_real_day_so_the_line_does_not_break() -> None:
    pmc, entries = small_history()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), projection(5))

    ctl_plan = next(t for t in figure.data if t.name == "Fitness (CTL), plan")
    assert ctl_plan.x[0] == pmc.days[-1]
    assert ctl_plan.y[0] == pytest.approx(float(pmc.ctl[-1]))
    assert ctl_plan.y[1] == pytest.approx(21.0)
    assert len(ctl_plan.x) == 6
    tsb_plan = next(t for t in figure.data if t.name == "Form (TSB), plan")
    assert tsb_plan.y[0] == pytest.approx(float(pmc.tsb[-1]))


def test_the_plan_uses_the_series_colours_of_the_real_part() -> None:
    pmc, entries = small_history()
    tokens = tokens_for(Theme.LIGHT)

    figure = build_pmc_figure(pmc, entries, LABELS, tokens, projection())

    by_name = {t.name: t for t in figure.data}
    assert by_name["Fitness (CTL), plan"].line.color == tokens.ctl
    assert by_name["Fatigue (ATL), plan"].line.color == tokens.atl
    assert by_name["Form (TSB), plan"].line.color == tokens.tsb


def test_the_form_projection_is_on_the_form_panel_and_the_load_ones_on_the_top_panel() -> None:
    pmc, entries = small_history()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), projection())

    by_name = {t.name: t for t in figure.data}
    assert by_name["Form (TSB), plan"].yaxis == "y2"
    assert by_name["Fitness (CTL), plan"].yaxis == "y"
    assert by_name["Races"].yaxis == "y"


def test_a_race_is_a_marker_with_its_priority_letter_on_the_projected_ctl() -> None:
    pmc, entries = small_history()
    proj = projection()

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), proj)

    races = next(t for t in figure.data if t.name == "Races")
    assert list(races.text) == ["A"]
    assert races.mode == "markers+text"  # the letter is in the chart, not only a colour
    assert races.x[0] == proj.races[0].day
    assert races.y[0] == pytest.approx(proj.points[19].ctl)
    assert races.customdata[0] == "Race A: Marathon"


def test_a_race_outside_the_projection_has_no_marker() -> None:
    pmc, entries = small_history()
    far = ProjectedRace(LAST_REAL + timedelta(days=400), Priority.B, "Half marathon")
    proj = PmcProjection(projection().points, (far,))

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), proj)

    assert "Races" not in [t.name for t in figure.data]


def test_a_race_name_is_escaped_in_the_hint() -> None:
    pmc, entries = small_history()
    day = LAST_REAL + timedelta(days=3)
    proj = PmcProjection(projection().points, (ProjectedRace(day, Priority.C, "<b>x</b> & y"),))

    figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(Theme.LIGHT), proj)

    hint = next(t for t in figure.data if t.name == "Races").customdata[0]
    assert "<b>" not in hint
    assert "&lt;b&gt;" in hint


def test_the_chart_opens_on_the_last_three_months_and_the_plan() -> None:
    first = date(2025, 1, 1)
    daily = {first + timedelta(days=i): 50.0 for i in range(0, 400, 2)}
    pmc = compute_pmc(daily, first, first + timedelta(days=399))
    last_real = pmc.days[-1]
    points = tuple(PmcPoint(last_real + timedelta(days=i), 40.0, 40.0, 0.0) for i in range(1, 100))

    figure = build_pmc_figure(pmc, [], LABELS, tokens_for(Theme.LIGHT), PmcProjection(points))

    low, high = figure.layout.xaxis.range
    assert str(low)[:10] == str(last_real - timedelta(days=90))
    assert str(high)[:10] == str(points[-1].day)
    shapes = figure.layout.shapes
    assert len(shapes) == 1  # the line between the real part and the plan
    assert shapes[0].line.dash == "dot"
    assert "Today" in [a.text for a in figure.layout.annotations]


def test_a_projection_in_both_themes_keeps_the_text_and_the_names() -> None:
    pmc, entries = small_history()

    for theme in Theme:
        figure = build_pmc_figure(pmc, entries, LABELS, tokens_for(theme), projection())
        assert figure.layout.paper_bgcolor == tokens_for(theme).background
        assert len(figure.data) == 8


def test_the_page_payload_with_a_projection_is_one_json_with_no_nan() -> None:
    pmc, entries = small_history()
    tokens = tokens_for(Theme.LIGHT)

    prepared = prepare_chart(pmc, entries, LABELS, tokens, projection())
    script = render_script(prepared.figure, CHROME, "x")

    assert script.startswith("ponosRender(")
    payload = json.loads(script.removeprefix("ponosRender(").removesuffix(");"))
    assert len(payload["figure"]["data"]) == 8
    assert np.isfinite(np.array(payload["figure"]["data"][4]["y"], dtype=float)).all()
