# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Scaffolding of the synchronisation tests: fake clock and wait, ZIP extractor and
fast decoding for the volumes, and a "rig" that puts the ports together.

No real `sleep`: the wait advances the fake clock and remembers how long it lasted. Standard
library plus the project types only; the ZIP extractor here is minimal and serves the tests (in the
product the Composition Root provides it with the ZIP adapter).
"""

from __future__ import annotations

import io
import zipfile
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    Activity,
    ActivityRecord,
    ActivitySummary,
    FailureReason,
    FileFormat,
    FileId,
    FitIdentity,
    GarminActivityId,
    RemoteActivity,
    Sport,
    SyncPolicy,
    SyncStatus,
)
from ponos_coach.activities.ports import (
    ActivityArchive,
    ActivityIndex,
    ActivitySource,
    SyncJournal,
)
from ponos_coach.activities.sync import (
    ActivityDecoder,
    ExtractedOriginal,
    OriginalExtractionError,
    OriginalExtractor,
    SyncMode,
    SyncOutcome,
    SyncPorts,
    SyncProgress,
    synchronize_activities,
)
from ponos_coach.profiles.domain import ProfileId, ProfileName
from tests.fakes.archive import InMemoryActivityArchive
from tests.fakes.garmin import FIRST_ACTIVITY_ID, FakeActivitySource, make_remote_history
from tests.fakes.profile_storage import InMemoryProfileStorage
from tests.fixtures.generators.import_files import RUN_SECONDS, run_start

CLOCK_START = datetime(2026, 7, 1, 8, 0, tzinfo=UTC)


class FakeSyncClock:
    """Fake clock: `advance` advances the instant and the monotonic time together."""

    def __init__(self) -> None:
        self._moment = CLOCK_START
        self._elapsed_s = 0.0

    def now(self) -> datetime:
        return self._moment

    def monotonic(self) -> float:
        return self._elapsed_s

    def advance(self, seconds: float) -> None:
        self._elapsed_s += seconds
        self._moment += timedelta(seconds=seconds)


class FakeWait:
    """Fake interruptible wait: records the duration, advances the clock, does not sleep.

    `cancel_on_wait(n)`: the n-th wait (from 1) turns out to be interrupted by the cancellation.
    """

    def __init__(self, clock: FakeSyncClock) -> None:
        self._clock = clock
        self.waits: list[float] = []
        self._cancel_at: int | None = None

    def cancel_on_wait(self, number: int) -> None:
        self._cancel_at = number

    def __call__(self, seconds: float) -> bool:
        self.waits.append(seconds)
        self._clock.advance(seconds)
        return self._cancel_at == len(self.waits)

    @property
    def total_s(self) -> float:
        return sum(self.waits)


class MidpointRandom:
    """Deterministic random source: the midpoint (the jitter is half of the maximum)."""

    def uniform(self, low: float, high: float) -> float:
        return (low + high) / 2.0


def extract_original(zipped: bytes) -> ExtractedOriginal:
    """Extracts the first `.fit` of a ZIP (minimal, for the tests)."""
    others = 0
    try:
        with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
            for info in archive.infolist():
                name = info.filename.lower()
                if name.endswith(".fit"):
                    return ExtractedOriginal(archive.read(info), FileFormat.FIT)
                others += 1
    except (zipfile.BadZipFile, zlib.error, EOFError):
        raise OriginalExtractionError(FailureReason.ARCHIVE_INVALID) from None
    raise OriginalExtractionError(
        FailureReason.UNSUPPORTED_FORMAT if others else FailureReason.NOT_AN_ACTIVITY
    )


def passthrough_extract(zipped: bytes) -> ExtractedOriginal:
    """For the volume tests: the "downloaded" bytes already are the file (a fake FIT)."""
    return ExtractedOriginal(zipped, FileFormat.FIT)


def fast_decode(content: bytes, file_format: FileFormat) -> Activity:
    """For the volume tests: the "file" is `run:<n>`; gives run `n` without reading a real FIT."""
    number = int(content.removeprefix(b"run:"))
    return Activity(
        FitIdentity(FileId(255, 1, number, run_start(0))),
        file_format,
        Sport.RUNNING,
        run_start(number),
        float(RUN_SECONDS),
        float(RUN_SECONDS),
        None,
        ActivitySummary(),
    )


def run_file(number: int) -> bytes:
    """The fake "file" of `fast_decode` for run `number`."""
    return f"run:{number}".encode()


def remote_id(number: int) -> GarminActivityId:
    """The Garmin id of run `number` of `make_remote_history`."""
    return GarminActivityId(FIRST_ACTIVITY_ID + number)


@dataclass(slots=True)
class SyncRig:
    """The ports of a test synchronisation, all observable.

    `source`, `archive`, `index` and `journal` can be replaced (with a decorator that
    records or makes them fail) before calling `run`; `memory` remains the real in-memory archive.
    """

    fake: FakeActivitySource
    source: ActivitySource
    index: ActivityIndex
    journal: SyncJournal
    archive: ActivityArchive
    memory: InMemoryActivityArchive
    clock: FakeSyncClock = field(default_factory=FakeSyncClock)
    policy: SyncPolicy = field(default_factory=SyncPolicy)
    extract: OriginalExtractor = extract_original
    decode: ActivityDecoder = decode_stored_activity
    wait: FakeWait = field(init=False)

    def __post_init__(self) -> None:
        self.wait = FakeWait(self.clock)

    def ports(self) -> SyncPorts:
        return SyncPorts(
            self.source,
            self.index,
            self.journal,
            self.archive,
            self.extract,
            self.decode,
            self.clock,
            self.wait,
            MidpointRandom(),
            self.policy,
        )

    def run(
        self,
        mode: SyncMode = SyncMode.SYNC,
        *,
        report: Callable[[SyncProgress], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> SyncOutcome:
        options: dict[str, object] = {}
        if report is not None:
            options["report"] = report
        if is_cancelled is not None:
            options["is_cancelled"] = is_cancelled
        return synchronize_activities(self.ports(), mode, **options)  # type: ignore[arg-type]

    def records(self) -> Sequence[ActivityRecord]:
        return self.index.list_by_start()

    def status_of(self, number: int) -> SyncStatus:
        entry = self.journal.get(remote_id(number))
        assert entry is not None, f"run {number} is not in the journal"
        return entry.status


def make_source(
    count: int, *, clock: FakeSyncClock | None = None, latency_s: float = 0.0, **options: object
) -> FakeActivitySource:
    """Fake Garmin with `count` runs; with `latency_s` every call advances the clock."""
    activities: list[RemoteActivity] = make_remote_history(count, **options)  # type: ignore[arg-type]
    return FakeActivitySource(
        activities,
        latency_s=latency_s,
        on_latency=clock.advance if clock is not None else None,
    )


def in_memory_rig(
    count: int = 0,
    *,
    policy: SyncPolicy | None = None,
    latency_s: float = 0.0,
    **source_options: object,
) -> SyncRig:
    """Rig with fake profile, archive and Garmin in memory; `count` runs on the fake Garmin."""
    clock = FakeSyncClock()
    profile = InMemoryProfileStorage().create(
        Path("profilo-di-prova"),
        ProfileId.parse("00000000-0000-4000-8000-000000000001"),
        ProfileName.parse("Atleta di prova"),
        "UTC",
    )
    fake = make_source(count, clock=clock, latency_s=latency_s, **source_options)
    memory = InMemoryActivityArchive()
    return SyncRig(
        fake,
        fake,
        profile.activities,
        profile.sync_journal,
        memory,
        memory,
        clock,
        policy or SyncPolicy(),
    )
