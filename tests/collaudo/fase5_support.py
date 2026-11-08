# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding for the phase 5 acceptance campaign: a real profile (SQLite, on-disk archive) in
which synthetic runs are imported as FIT files, with the goals, the plan and the nutrition use cases
wired the way the application wires them.

Every value comes from an invented athlete ("Atleta di prova"): 180 cm, 80 kg, born 1990-05-05,
heart rate 50-190 bpm, threshold 170 bpm, male.
"""

from __future__ import annotations

import itertools
import shutil
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import ActivityRecord, Sport
from ponos_coach.goals.domain import (
    GoalId,
    Priority,
    RaceGoal,
    SessionChoices,
    SessionType,
)
from ponos_coach.goals.use_cases import AddGoal, SetAvailability, SetSessionChoices
from ponos_coach.load.domain import PhysiologyEntry
from ponos_coach.load.use_cases import ComputeProfileOverview, ProfileOverview
from ponos_coach.planning.domain import Plan
from ponos_coach.planning.use_cases import CurrentPlan, PastPlan
from ponos_coach.profiles.day_nutrition import (
    DayNutritionReport,
    DayNutritionResult,
    NutritionSources,
    PlanContext,
    PlanDayNutrition,
)
from ponos_coach.profiles.domain import BirthDate, FixedProfileData, Height, Weight, WeightEntry
from ponos_coach.profiles.rebuild_plan import (
    PlanAdherence,
    PlanBuilt,
    RebuildPlan,
    plan_sources_of,
)
from tests.builders import physiology
from tests.collaudo.fase4_support import Leg, run_fit
from tests.collaudo.support import (
    RealProfile,
    create_profile,
    open_profile_from_folder,
    real_ports,
)
from tests.food_support import load_food_table
from tests.planning.builders import BIG_WEEK

ROME = ZoneInfo("Europe/Rome")
TODAY = date(2026, 3, 2)  # a Monday, like the plan builders' `TODAY`
FIRST_DAY = date(2026, 1, 1)
BORN = date(1990, 5, 5)
THRESHOLD_BPM = 170
ALL_TYPES = frozenset(SessionType)
FOODS = load_food_table()
_SERIALS = itertools.count(7_000_001)


def local_start(day: date, hour: int = 7, minute: int = 0) -> datetime:
    """A local time in Rome as an instant."""
    return datetime.combine(day, time(hour, minute), tzinfo=ROME).astimezone(UTC)


def race(
    name: str,
    day: date,
    priority: Priority = Priority.A,
    distance_m: int = 10_000,
    target_time_s: int | None = None,
) -> RaceGoal:
    return RaceGoal(GoalId(name), day, priority, Sport.RUNNING, distance_m, target_time_s)


@dataclass
class World:
    """A real profile with the body data, the use cases of phase 5 and a movable `today`."""

    profile: RealProfile
    base: Path
    today: date = TODAY
    seed: bool = True

    def __post_init__(self) -> None:
        session = self.profile.session
        self.add_goal = AddGoal(session.goals)
        self.set_availability = SetAvailability(session.activities, session.availability)
        self.set_choices = SetSessionChoices(session.activities, session.session_choices)
        if self.seed:
            session.physiology.put(PhysiologyEntry(FIRST_DAY, physiology(THRESHOLD_BPM)))
            session.fixed_data.set_once(FixedProfileData(Height(180), BirthDate(BORN)))
            session.weights.put(WeightEntry(FIRST_DAY, Weight(800)))
            self.set_availability(BIG_WEEK)
            self.set_choices(SessionChoices.of({Sport.RUNNING: ALL_TYPES}))
        self.rebuild = RebuildPlan(plan_sources_of(session), self.overview)
        self.current = CurrentPlan(session.plans)
        self.past = PastPlan(session.plans)
        self.adherence = PlanAdherence(session.plans, self.overview, lambda: self.today)

    # -- data -----------------------------------------------------------------------------------
    def overview(self) -> ProfileOverview:
        session = self.profile.session
        return ComputeProfileOverview(
            session.activities,
            self.profile.archive,
            session.physiology,
            session.load_cache,
            decode_stored_activity,
            sport_weights=session.sport_weights,
            timezone=ROME,
            today=lambda: self.today,
        ).execute()

    def import_run(
        self, day: date, *, minutes: int = 45, bpm: int = 150, hour: int = 7, speed: float = 3.2
    ) -> ActivityRecord:
        """Imports one run of `minutes` at a constant `bpm` through the real import."""
        serial = next(_SERIALS)
        data = run_fit([Leg(minutes * 60, bpm, speed)], local_start(day, hour), serial=serial)
        directory = self.base / "incoming" / str(serial)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"run-{serial}.fit"
        path.write_bytes(data)
        before = {r.identity for r in self.profile.records()}
        summary = self.profile.import_files([path])
        assert summary.results[0].issue is None, summary.results[0]
        [record] = [r for r in self.profile.records() if r.identity not in before]
        return record

    def import_history(self, first: date, last: date, *, every: int = 2, bpm: int = 150) -> None:
        day = first
        while day <= last:
            self.import_run(day, bpm=bpm)
            day += timedelta(days=every)

    # -- plan -----------------------------------------------------------------------------------
    def built(self) -> PlanBuilt:
        result = self.rebuild(self.today)
        assert isinstance(result, PlanBuilt), result
        return result

    def saved_plan(self) -> Plan:
        stored = self.current()
        assert stored is not None
        assert isinstance(stored.state, Plan)
        return stored.state

    # -- nutrition ------------------------------------------------------------------------------
    def nutrition(self, day: date) -> DayNutritionResult:
        s = self.profile.session
        sources = NutritionSources(
            s.activities,
            self.profile.archive,
            s.load_cache,
            s.physiology,
            s.sport_weights,
            s.weights,
            s.fixed_data,
            s.activity_levels,
            s.meal_times,
            s.goal_periods,
            s.lean_masses,
        )
        return PlanDayNutrition(
            sources,
            decode_stored_activity,
            ROME,
            FOODS,
            plan_context=PlanContext(s.plans, self.today),
        )(day)

    def report(self, day: date) -> DayNutritionReport:
        result = self.nutrition(day)
        assert isinstance(result, DayNutritionReport), result
        return result

    def close(self) -> None:
        self.profile.close()


def make_world(base: Path, *, today: date = TODAY) -> World:
    """A new profile in `base` with the body data, a week of availability and every session type."""
    return World(create_profile(base), base, today)


def reopen_world(base: Path, *, today: date = TODAY) -> World:
    """The profile of `base` opened again (after a close, a copy or a hand edit of the file)."""
    profile = open_profile_from_folder(real_ports(base / "config"), base / "profilo")
    return World(profile, base, today, seed=False)


def clone_world(template: Path, destination: Path, *, today: date = TODAY) -> World:
    """A copy of a closed profile folder, opened. Cheaper than importing the history again."""
    shutil.copytree(template, destination)
    return reopen_world(destination, today=today)
