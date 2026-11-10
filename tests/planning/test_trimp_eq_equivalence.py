# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""The move from hrTSS (with sport weights) to TRIMPeq changes no running plan.

`golden/plans_hrtss.json` was first recorded with the engine of method version 6 and re-recorded
with version 10 (the heart-rate zones of the user, 2026-11-10: the heart-rate ranges of the blocks,
the representative heart rate of Z1 and so the estimated loads and some durations moved; the
session types, the days, the phases and the warnings did not) on the scenarios of
`equivalence_scenarios`: for the same data and a running athlete the sessions (type, duration,
days, zones, heart-rate ranges), the phases, the weeks and the warnings are the recorded ones, and
every load is the recorded number. To re-record after a change of the engine on purpose, build
each scenario with `build_plan(inputs(**kwargs), TODAY)`, take `summary(plan)` and dump the
dictionary by scenario name with sorted keys and compact separators.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ponos_coach.planning.domain import Plan, build_plan
from tests.planning.builders import TODAY, inputs
from tests.planning.equivalence_scenarios import scenarios, summary

GOLDEN = json.loads((Path(__file__).parent / "golden" / "plans_hrtss.json").read_text("utf-8"))
REL_TOL = 1e-9  # float rounding only: the loads are computed by the same formula


def test_every_recorded_scenario_is_still_a_scenario() -> None:
    assert sorted(GOLDEN) == sorted(scenarios())


@pytest.mark.parametrize("name", sorted(GOLDEN))
def test_the_running_plan_is_the_one_recorded_with_hrtss(name: str) -> None:
    plan = build_plan(inputs(**scenarios()[name]), TODAY)
    assert isinstance(plan, Plan)

    now = summary(plan)

    assert now["shape"] == GOLDEN[name]["shape"]
    for kind, recorded in GOLDEN[name]["loads"].items():
        assert now["loads"][kind] == pytest.approx(recorded, rel=REL_TOL, abs=1e-9), kind
