# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""In-memory profile database: `ProfileStorage`, `OpenProfile` and the stores.

The data of each profile live in the `InMemoryDisk` (like the database file): closing and
reopening the profile finds them again. A transaction saves a copy of the data and restores it if
it ends with an exception.
"""

from __future__ import annotations

import copy
from collections.abc import Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType

from ponos_coach.activities.domain import (
    ActivityIdentity,
    ActivityRecord,
    FailureReason,
    GarminActivityId,
    GeoBox,
    InvalidTransitionError,
    JournalEntry,
    ListingCursor,
    ListingKind,
    RemoteActivity,
    Sport,
    SyncStatus,
    can_transition,
    identity_key,
    is_possible_duplicate,
    pending_entry,
    track_box_of,
)
from ponos_coach.activities.ports import (
    ActivityIndex,
    ActivityNotFoundError,
    BoxCandidate,
    DuplicateActivityError,
    IndexedActivity,
    IndexRevision,
    SyncJournal,
    TrackBoxIndex,
    TransactionStateError,
    UnknownJournalEntryError,
)
from ponos_coach.assessment.domain import ThresholdTest
from ponos_coach.assessment.ports import (
    ThresholdTestAlreadyStoredError,
    ThresholdTestNotFoundError,
    ThresholdTestStore,
)
from ponos_coach.goals.domain import GoalId, RaceGoal, SessionChoices, WeeklyAvailability
from ponos_coach.goals.ports import (
    AvailabilityStore,
    Goal,
    GoalAlreadyExistsError,
    GoalNotFoundError,
    GoalStore,
    RaceDayTakenError,
    SessionChoicesStore,
)
from ponos_coach.load.domain import ActivityLoad, LoadFingerprint, PhysiologyEntry, SportWeight
from ponos_coach.load.ports import (
    LoadCache,
    PhysiologyEntryNotFoundError,
    PhysiologyStore,
    SportWeightStore,
    UnknownActivityError,
)
from ponos_coach.nutrition.domain import ActivityLevelEntry, GoalPeriod, LeanMassEntry, MealTimes
from ponos_coach.nutrition.ports import (
    ActivityLevelNotFoundError,
    ActivityLevelStore,
    GoalPeriodNotFoundError,
    GoalPeriodOverlapError,
    GoalPeriodStore,
    LeanMassNotFoundError,
    LeanMassStore,
    MealTimesStore,
)
from ponos_coach.planning.domain import Plan, PlanRefusal
from ponos_coach.planning.ports import FrozenDay, PlanStore, StoredPlan, UnreadablePlan
from ponos_coach.profiles.domain import (
    PROFILE_DATABASE_NAME,
    FixedProfileData,
    FolderKind,
    ProfileId,
    ProfileInfo,
    ProfileName,
    ProfilePeek,
    WeightEntry,
)
from ponos_coach.profiles.ports import (
    FixedDataAlreadySetError,
    FixedDataStore,
    OpenProfile,
    ProfileAlreadyExistsError,
    ProfileNotFoundError,
    WeightStore,
)
from ponos_coach.profiles.timezones import require_known_timezone

FORMAT_VERSION = 6


@dataclass
class _Database:
    """Content of a profile's database."""

    info: ProfileInfo
    records: dict[str, ActivityRecord] = field(default_factory=dict)  # by identity key
    track_boxes: dict[str, GeoBox] = field(default_factory=dict)  # by identity key
    garmin_ids: set[int] = field(default_factory=set)
    physiology: dict[date, PhysiologyEntry] = field(default_factory=dict)
    weights: dict[date, WeightEntry] = field(default_factory=dict)
    fixed: FixedProfileData | None = None
    sport_weights: dict[Sport, SportWeight] = field(default_factory=dict)
    loads: dict[str, tuple[LoadFingerprint, ActivityLoad]] = field(default_factory=dict)
    levels: dict[date, ActivityLevelEntry] = field(default_factory=dict)
    lean_masses: dict[date, LeanMassEntry] = field(default_factory=dict)
    meal_times: MealTimes | None = None
    goals: dict[date, GoalPeriod] = field(default_factory=dict)
    threshold_tests: dict[str, ThresholdTest] = field(default_factory=dict)  # by identity key
    calendar_goals: dict[str, Goal] = field(default_factory=dict)  # by goal id
    availability: WeeklyAvailability | None = None
    session_choices: SessionChoices = field(default_factory=lambda: SessionChoices.of({}))
    plan_state: tuple[Plan | PlanRefusal, date] | None = None
    past_days: dict[date, FrozenDay] = field(default_factory=dict)
    unreadable_past_days: set[date] = field(default_factory=set)  # frozen, lost: set by a test
    cursors: dict[ListingKind, ListingCursor] = field(default_factory=dict)
    journal: dict[GarminActivityId, JournalEntry] = field(default_factory=dict)
    last_completed: datetime | None = None
    in_transaction: bool = False

    def snapshot(self) -> tuple[object, ...]:
        """Copy of the data a transaction can modify (immutable types: the copy is enough)."""
        return copy.deepcopy(
            (
                self.records,
                self.track_boxes,
                self.garmin_ids,
                self.physiology,
                self.weights,
                self.fixed,
                self.sport_weights,
                self.loads,
                self.levels,
                self.lean_masses,
                self.meal_times,
                self.goals,
                self.threshold_tests,
                self.calendar_goals,
                self.availability,
                self.session_choices,
                self.plan_state,
                self.past_days,
                self.cursors,
                self.journal,
                self.last_completed,
            )
        )

    def restore(self, snapshot: tuple[object, ...]) -> None:
        """Put the data back as they were in the copy."""
        (
            self.records,
            self.track_boxes,
            self.garmin_ids,
            self.physiology,
            self.weights,
            self.fixed,
            self.sport_weights,
            self.loads,
            self.levels,
            self.lean_masses,
            self.meal_times,
            self.goals,
            self.threshold_tests,
            self.calendar_goals,
            self.availability,
            self.session_choices,
            self.plan_state,
            self.past_days,
            self.cursors,
            self.journal,
            self.last_completed,
        ) = snapshot  # type: ignore[assignment]


@dataclass
class InMemoryDisk:
    """Fake disk shared by folders and databases: contained names, kinds, locks, databases."""

    folders: dict[Path, set[str]] = field(default_factory=dict)
    kinds: dict[Path, FolderKind] = field(default_factory=dict)
    locks: set[Path] = field(default_factory=set)
    databases: dict[Path, _Database] = field(default_factory=dict)

    def kind_of(self, folder: Path) -> FolderKind:
        """Kind of the folder or of its nearest ancestor with a declared kind."""
        for candidate in (folder, *folder.parents):
            if candidate in self.kinds:
                return self.kinds[candidate]
        return FolderKind.LOCAL


class _Index:
    def __init__(self, database: _Database) -> None:
        self._db = database

    @contextmanager
    def transaction(self) -> Iterator[None]:
        db = self._db
        if db.in_transaction:
            raise TransactionStateError("transazione annidata")
        snapshot = db.snapshot()
        db.in_transaction = True
        try:
            yield
        except BaseException:
            db.restore(snapshot)
            raise
        finally:
            db.in_transaction = False

    def exists(self, identity: ActivityIdentity) -> bool:
        return identity_key(identity) in self._db.records

    def get(self, identity: ActivityIdentity) -> ActivityRecord | None:
        return self._db.records.get(identity_key(identity))

    def find_possible_duplicates(
        self, start: datetime, elapsed_s: float
    ) -> Sequence[ActivityRecord]:
        return [
            r
            for r in self.list_by_start()
            if is_possible_duplicate(start, elapsed_s, r.start, r.elapsed_s)
        ]

    def add(self, record: ActivityRecord) -> None:
        key = identity_key(record.identity)
        garmin_id = record.garmin_activity_id
        if key in self._db.records or (garmin_id is not None and garmin_id in self._db.garmin_ids):
            raise DuplicateActivityError(key)
        self._db.records[key] = record
        try:  # like the real index: a polyline that cannot be read gives no box
            box = track_box_of(record.summary.start_point, record.summary.track_polyline)
        except ValueError:
            box = None
        if box is not None:
            self._db.track_boxes[key] = box
        if garmin_id is not None:
            self._db.garmin_ids.add(garmin_id)

    def list_by_start(self) -> Sequence[ActivityRecord]:
        return sorted(self._db.records.values(), key=lambda r: (r.start, identity_key(r.identity)))

    def list_started_between(self, first: datetime, last: datetime) -> Sequence[ActivityRecord]:
        inside = [r for r in self._db.records.values() if first <= r.start < last]
        return sorted(inside, key=lambda r: (r.start, identity_key(r.identity)))

    def first_start_at_or_after(self, instant: datetime) -> datetime | None:
        starts = [r.start for r in self._db.records.values() if r.start >= instant]
        return min(starts, default=None)

    def find_by_garmin_id(self, garmin_id: GarminActivityId) -> ActivityRecord | None:
        for record in self._db.records.values():
            if record.garmin_activity_id == garmin_id.value:
                return record
        return None

    def link_garmin_id(self, identity: ActivityIdentity, garmin_id: GarminActivityId) -> None:
        key = identity_key(identity)
        record = self._db.records.get(key)
        if record is None:
            raise ActivityNotFoundError(key)
        if record.garmin_activity_id == garmin_id.value:
            return
        if record.garmin_activity_id is not None or garmin_id.value in self._db.garmin_ids:
            raise DuplicateActivityError(key)
        self._db.records[key] = replace(record, garmin_activity_id=garmin_id.value)
        self._db.garmin_ids.add(garmin_id.value)


class _Journal:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def load_cursor(self, kind: ListingKind) -> ListingCursor | None:
        return self._db.cursors.get(kind)

    def save_cursor(self, kind: ListingKind, cursor: ListingCursor) -> None:
        self._db.cursors[kind] = cursor

    def known_ids(self, ids: Collection[GarminActivityId]) -> frozenset[GarminActivityId]:
        return frozenset(i for i in ids if i in self._db.journal)

    def add_pending(self, items: Sequence[RemoteActivity], now: datetime) -> int:
        added = 0
        for item in items:
            if item.garmin_id not in self._db.journal:
                self._db.journal[item.garmin_id] = pending_entry(item, now)
                added += 1
        return added

    def next_to_process(self, limit: int) -> Sequence[JournalEntry]:
        if limit < 1:
            raise ValueError("the limit must be at least 1")
        todo = [
            e
            for e in self._db.journal.values()
            if e.status in (SyncStatus.PENDING, SyncStatus.RETRYABLE)
        ]
        todo.sort(key=lambda e: (e.remote.start, e.garmin_id.value), reverse=True)
        return todo[:limit]

    def next_exhausted(self, limit: int) -> Sequence[JournalEntry]:
        if limit < 1:
            raise ValueError("the limit must be at least 1")
        exhausted = [
            e
            for e in self._db.journal.values()
            if e.status is SyncStatus.FAILED and e.failure is FailureReason.ATTEMPTS_EXHAUSTED
        ]
        exhausted.sort(key=lambda e: (e.remote.start, e.garmin_id.value), reverse=True)
        return exhausted[:limit]

    def get(self, garmin_id: GarminActivityId) -> JournalEntry | None:
        return self._db.journal.get(garmin_id)

    def update(self, entry: JournalEntry) -> None:
        stored = self._db.journal.get(entry.garmin_id)
        if stored is None:
            raise UnknownJournalEntryError(str(entry.garmin_id))
        if not can_transition(stored.status, entry.status):
            raise InvalidTransitionError(f"{stored.status.value} -> {entry.status.value}")
        self._db.journal[entry.garmin_id] = JournalEntry(
            stored.remote, entry.status, entry.updated_at, entry.failed_attempts, entry.failure
        )

    def counts(self) -> Mapping[SyncStatus, int]:
        counts = dict.fromkeys(SyncStatus, 0)
        for entry in self._db.journal.values():
            counts[entry.status] += 1
        return counts

    def last_completed_at(self) -> datetime | None:
        return self._db.last_completed

    def record_completed(self, when: datetime) -> None:
        self._db.last_completed = when


class _Physiology:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def put(self, entry: PhysiologyEntry) -> None:
        self._db.physiology[entry.valid_from] = entry

    def remove(self, valid_from: date) -> None:
        if valid_from not in self._db.physiology:
            raise PhysiologyEntryNotFoundError("missing")
        del self._db.physiology[valid_from]

    def list_entries(self) -> Sequence[PhysiologyEntry]:
        return [self._db.physiology[day] for day in sorted(self._db.physiology)]


class _Weights:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def put(self, entry: WeightEntry) -> None:
        self._db.weights[entry.valid_from] = entry

    def list_entries(self) -> Sequence[WeightEntry]:
        return [self._db.weights[day] for day in sorted(self._db.weights)]


class _FixedData:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self) -> FixedProfileData | None:
        return self._db.fixed

    def set_once(self, data: FixedProfileData) -> None:
        if self._db.fixed is not None:
            raise FixedDataAlreadySetError("already set")
        self._db.fixed = data


class _SportWeights:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def load(self) -> Mapping[Sport, SportWeight]:
        return MappingProxyType(dict(self._db.sport_weights))

    def replace(self, weights: Mapping[Sport, SportWeight]) -> None:
        self._db.sport_weights = dict(weights)


class _Cache:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self, identity: ActivityIdentity, fingerprint: LoadFingerprint) -> ActivityLoad | None:
        stored = self._db.loads.get(identity_key(identity))
        return stored[1] if stored is not None and stored[0] == fingerprint else None

    def put(
        self, identity: ActivityIdentity, fingerprint: LoadFingerprint, load: ActivityLoad
    ) -> None:
        key = identity_key(identity)
        if key not in self._db.records:
            raise UnknownActivityError(key)
        self._db.loads[key] = (fingerprint, load)

    def invalidate(self, identity: ActivityIdentity) -> None:
        self._db.loads.pop(identity_key(identity), None)

    def clear(self) -> None:
        self._db.loads.clear()


class _ActivityLevels:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def put(self, entry: ActivityLevelEntry) -> None:
        self._db.levels[entry.valid_from] = entry

    def remove(self, valid_from: date) -> None:
        if valid_from not in self._db.levels:
            raise ActivityLevelNotFoundError("missing")
        del self._db.levels[valid_from]

    def list_entries(self) -> Sequence[ActivityLevelEntry]:
        return [self._db.levels[day] for day in sorted(self._db.levels)]


class _LeanMasses:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def put(self, entry: LeanMassEntry) -> None:
        self._db.lean_masses[entry.valid_from] = entry

    def remove(self, valid_from: date) -> None:
        if valid_from not in self._db.lean_masses:
            raise LeanMassNotFoundError("missing")
        del self._db.lean_masses[valid_from]

    def list_entries(self) -> Sequence[LeanMassEntry]:
        return [self._db.lean_masses[day] for day in sorted(self._db.lean_masses)]


class _MealTimes:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self) -> MealTimes | None:
        return self._db.meal_times

    def set(self, times: MealTimes) -> None:
        self._db.meal_times = times


class _GoalPeriods:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def list_periods(self) -> Sequence[GoalPeriod]:
        return [self._db.goals[day] for day in sorted(self._db.goals)]

    def add(self, period: GoalPeriod) -> None:
        if any(period.overlaps(other) for other in self._db.goals.values()):
            raise GoalPeriodOverlapError("overlap")
        self._db.goals[period.first_day] = period

    def remove(self, first_day: date) -> None:
        if first_day not in self._db.goals:
            raise GoalPeriodNotFoundError("missing")
        del self._db.goals[first_day]


class _ThresholdTests:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def add(self, test: ThresholdTest) -> None:
        key = identity_key(test.identity)
        if key in self._db.threshold_tests:
            raise ThresholdTestAlreadyStoredError("already a test")
        self._db.threshold_tests[key] = test

    def remove(self, identity: ActivityIdentity) -> None:
        key = identity_key(identity)
        if key not in self._db.threshold_tests:
            raise ThresholdTestNotFoundError("missing")
        del self._db.threshold_tests[key]

    def get(self, identity: ActivityIdentity) -> ThresholdTest | None:
        return self._db.threshold_tests.get(identity_key(identity))

    def list_tests(self) -> Sequence[ThresholdTest]:
        ordered = sorted(
            self._db.threshold_tests.items(),
            key=lambda item: (item[1].test_day, item[1].segment_start, item[0]),
        )
        return [test for _, test in ordered]


class _CalendarGoals:
    """Goals: like the SQLite store, one race a day."""

    def __init__(self, database: _Database) -> None:
        self._db = database

    def _race_day_taken(self, goal: Goal) -> bool:
        if not isinstance(goal, RaceGoal):
            return False
        return any(
            isinstance(other, RaceGoal) and other.day == goal.day and other.goal_id != goal.goal_id
            for other in self._db.calendar_goals.values()
        )

    def add(self, goal: Goal) -> None:
        if goal.goal_id.value in self._db.calendar_goals:
            raise GoalAlreadyExistsError("id taken")
        if self._race_day_taken(goal):
            raise RaceDayTakenError("race day taken")
        self._db.calendar_goals[goal.goal_id.value] = goal

    def replace(self, goal: Goal) -> None:
        if goal.goal_id.value not in self._db.calendar_goals:
            raise GoalNotFoundError("missing")
        if self._race_day_taken(goal):
            raise RaceDayTakenError("race day taken")
        self._db.calendar_goals[goal.goal_id.value] = goal

    def remove(self, goal_id: GoalId) -> None:
        if goal_id.value not in self._db.calendar_goals:
            raise GoalNotFoundError("missing")
        del self._db.calendar_goals[goal_id.value]

    def get(self, goal_id: GoalId) -> Goal | None:
        return self._db.calendar_goals.get(goal_id.value)

    def list_goals(self) -> Sequence[Goal]:
        return sorted(self._db.calendar_goals.values(), key=lambda g: (g.day, g.goal_id.value))


class _Availability:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self) -> WeeklyAvailability | None:
        return self._db.availability

    def set(self, availability: WeeklyAvailability | None) -> None:
        self._db.availability = availability


class _SessionChoices:
    def __init__(self, database: _Database) -> None:
        self._db = database

    def get(self) -> SessionChoices:
        return self._db.session_choices

    def replace(self, choices: SessionChoices) -> None:
        self._db.session_choices = choices


class _Plans:
    """The plan store: a frozen day is never overwritten (first freeze wins)."""

    def __init__(self, database: _Database) -> None:
        self._db = database

    def current(self) -> StoredPlan | UnreadablePlan | None:
        saved = self._db.plan_state
        return None if saved is None else StoredPlan(*saved)

    def replace_current(self, state: Plan | PlanRefusal, generated_on: date) -> None:
        if isinstance(state, Plan) and state.generated_on != generated_on:
            raise ValueError("the plan was generated on another date")
        self._db.plan_state = (state, generated_on)

    def freeze(self, days: Sequence[FrozenDay]) -> None:
        for frozen in days:
            self._db.past_days.setdefault(frozen.day.day, frozen)

    def past_days(self, first: date, last: date) -> Sequence[FrozenDay]:
        return [
            f
            for d, f in sorted(self._db.past_days.items())
            if first <= d <= last and d not in self._db.unreadable_past_days
        ]

    def unreadable_past_days(self, first: date, last: date) -> Sequence[date]:
        return sorted(d for d in self._db.unreadable_past_days if first <= d <= last)


class _TrackBoxes:
    """Track boxes keyed by identity key; the row id is the 1-based insertion order."""

    def __init__(self, database: _Database) -> None:
        self._db = database

    def transaction(self) -> AbstractContextManager[None]:
        return _Index(self._db).transaction()

    def revision(self) -> IndexRevision:
        records = self._db.records
        return IndexRevision(len(records), len(records), len(self._db.track_boxes))

    def list_by_start(self) -> Sequence[IndexedActivity]:
        ordered = _Index(self._db).list_by_start()
        return [
            IndexedActivity(r, self._db.track_boxes.get(identity_key(r.identity))) for r in ordered
        ]

    def _missing(self) -> list[tuple[int, str, ActivityRecord]]:
        return [
            (row_id, key, record)
            for row_id, (key, record) in enumerate(self._db.records.items(), start=1)
            if key not in self._db.track_boxes
            and (
                record.summary.start_point is not None or record.summary.track_polyline is not None
            )
        ]

    def count_without_box(self) -> int:
        return len(self._missing())

    def candidates_without_box(self, after_row_id: int, limit: int) -> Sequence[BoxCandidate]:
        return [
            BoxCandidate(row_id, r.summary.start_point, r.summary.track_polyline)
            for row_id, _, r in self._missing()
            if row_id > after_row_id
        ][:limit]

    def store_boxes(self, boxes: Mapping[int, GeoBox]) -> None:
        keys = list(self._db.records)
        for row_id, box in boxes.items():
            self._db.track_boxes.setdefault(keys[row_id - 1], box)


class _OpenProfile:
    def __init__(self, database: _Database) -> None:
        self._db = database
        self._index = _Index(database)
        self._boxes = _TrackBoxes(database)
        self._journal = _Journal(database)
        self._physiology = _Physiology(database)
        self._weights = _Weights(database)
        self._fixed_data = _FixedData(database)
        self._sport_weights = _SportWeights(database)
        self._cache = _Cache(database)
        self._levels = _ActivityLevels(database)
        self._lean_masses = _LeanMasses(database)
        self._meal_times = _MealTimes(database)
        self._goals = _GoalPeriods(database)
        self._threshold_tests = _ThresholdTests(database)
        self._calendar_goals = _CalendarGoals(database)
        self._availability = _Availability(database)
        self._session_choices = _SessionChoices(database)
        self._plans = _Plans(database)

    @property
    def info(self) -> ProfileInfo:
        return self._db.info

    @property
    def activities(self) -> ActivityIndex:
        return self._index

    @property
    def track_boxes(self) -> TrackBoxIndex:
        return self._boxes

    @property
    def sync_journal(self) -> SyncJournal:
        return self._journal

    @property
    def physiology(self) -> PhysiologyStore:
        return self._physiology

    @property
    def weights(self) -> WeightStore:
        return self._weights

    @property
    def fixed_data(self) -> FixedDataStore:
        return self._fixed_data

    @property
    def sport_weights(self) -> SportWeightStore:
        return self._sport_weights

    @property
    def load_cache(self) -> LoadCache:
        return self._cache

    @property
    def activity_levels(self) -> ActivityLevelStore:
        return self._levels

    @property
    def lean_masses(self) -> LeanMassStore:
        return self._lean_masses

    @property
    def meal_times(self) -> MealTimesStore:
        return self._meal_times

    @property
    def goal_periods(self) -> GoalPeriodStore:
        return self._goals

    @property
    def threshold_tests(self) -> ThresholdTestStore:
        return self._threshold_tests

    @property
    def goals(self) -> GoalStore:
        return self._calendar_goals

    @property
    def availability(self) -> AvailabilityStore:
        return self._availability

    @property
    def session_choices(self) -> SessionChoicesStore:
        return self._session_choices

    @property
    def plans(self) -> PlanStore:
        return self._plans

    def close(self) -> None:
        """No effect: the data stay in the fake disk."""


class InMemoryProfileStorage:
    """`ProfileStorage` on an `InMemoryDisk`."""

    def __init__(self, disk: InMemoryDisk | None = None) -> None:
        self.disk = disk or InMemoryDisk()

    def create(
        self, folder: Path, profile_id: ProfileId, name: ProfileName, timezone_name: str
    ) -> OpenProfile:
        """See `ProfileStorage.create`."""
        if folder in self.disk.databases:
            raise ProfileAlreadyExistsError(folder.name)
        require_known_timezone(timezone_name)
        info = ProfileInfo(profile_id, name, timezone_name, FORMAT_VERSION)
        database = _Database(info)
        self.disk.databases[folder] = database
        self.disk.folders.setdefault(folder, set()).add(PROFILE_DATABASE_NAME)
        return _OpenProfile(database)

    def peek(self, folder: Path) -> ProfilePeek:
        """See `ProfileStorage.peek`."""
        database = self.disk.databases.get(folder)
        if database is None:
            raise ProfileNotFoundError(folder.name)
        return ProfilePeek(database.info, len(database.records))

    def open(self, folder: Path, *, check_integrity: bool = True) -> OpenProfile:
        """See `ProfileStorage.open` (integrity checks make no sense in memory)."""
        del check_integrity
        database = self.disk.databases.get(folder)
        if database is None:
            raise ProfileNotFoundError(folder.name)
        return _OpenProfile(database)
