# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Cross scenarios of phase 3, by an independent tester.

- a file of the archive disappears while the detail is open and while it is being calculated;
- the sport weights change during a synchronisation (even between one activity and the
  next): in the end every load is the one from the calculation from scratch;
- several edits of measurements and weights on the same overview already in cache.
"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QSettings
from pytestqt.qtbot import QtBot

from ponos_coach.activities.decoding import decode_stored_activity, decode_stored_track
from ponos_coach.activities.domain import (
    ActivityRecord,
    ArchivedFile,
)
from ponos_coach.load.activity_detail import GetActivityDetail
from ponos_coach.load.use_cases import (
    ComputeProfileOverview,
    ExcludedActivity,
    ExclusionReason,
)
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_state import Ready
from ponos_coach.ui.workspace import ProfileAccess
from tests.collaudo.fase3_support import profile_with_mixed
from tests.ui.test_activity_detail_window import open_detail
from tests.ui.test_profile_data_window import (
    PROFILE,
    window_with_activities,
)
from tests.ui.workspace_support import (
    close_window,
    make_settings,
)

WAIT_MS = 60_000
D_ERRORS = ("errorMessage", "unexpectedError")


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return make_settings(tmp_path)


@pytest.fixture
def gate() -> Iterator[threading.Event]:
    """A gate that always opens at the end of the test: no thread stays stuck."""
    event = threading.Event()
    yield event
    event.set()
    for thread in [t for t in threading.enumerate() if t.name.startswith("ponos-garmin-")]:
        thread.join(30)


def failure_boxes(window: object) -> list[str]:
    return [b.objectName() for b in window.open_boxes if b.objectName() in D_ERRORS]  # type: ignore[attr-defined]


# --- File that disappears ----------------------------------------------------------------------


def forget_cached_loads(qtbot: QtBot, window: MainWindow) -> None:
    """Empty the load cache on the data thread: the next overview reads every file again."""
    done: list[object] = []

    def work(access: ProfileAccess) -> None:
        with access.session.activities.transaction():
            access.session.load_cache.clear()

    assert window.controller.run_on_profile(work, done.append)
    qtbot.waitUntil(lambda: bool(done), timeout=WAIT_MS)


def test_a_file_that_disappears_with_the_detail_open_leaves_every_window_usable_and_recoverable(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    window = window_with_activities(qtbot, tmp_path, settings)
    archive = tmp_path / "profiles" / PROFILE / PROFILE_ARCHIVE_DIR
    window.activities_page.table.selectRow(0)
    window.detail_action.trigger()
    dialog = open_detail(qtbot, window)
    qtbot.waitUntil(dialog.shows_chart, timeout=WAIT_MS)
    stash = tmp_path / "stash"
    shutil.copytree(archive, stash)
    for original in [p for p in archive.rglob("*") if p.is_file()]:
        original.unlink()

    with caplog.at_level(logging.WARNING):
        forget_cached_loads(qtbot, window)  # every load is recalculated from its file
        window.controller.recalculate_overview()
        qtbot.waitUntil(
            lambda: (
                isinstance(window.controller.overview_state, Ready)
                and len(window.controller.overview_state.overview.excluded) == 8
            ),
            timeout=WAIT_MS,
        )

    state = window.controller.overview_state
    assert isinstance(state, Ready)
    assert state.overview.activities == ()  # no invented load without the file
    assert {e.reason for e in state.overview.excluded} == {ExclusionReason.FILE_MISSING}
    assert failure_boxes(window) == []
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert dialog.isVisible()  # the window opened before does not collapse
    shutil.copytree(stash, archive, dirs_exist_ok=True)  # the file comes back
    window.controller.recalculate_overview()
    qtbot.waitUntil(
        lambda: (
            isinstance(window.controller.overview_state, Ready)
            and len(window.controller.overview_state.overview.activities) == 8
        ),
        timeout=WAIT_MS,
    )
    back = window.controller.overview_state
    assert isinstance(back, Ready)
    assert back.overview.excluded == ()
    dialog.close()
    close_window(qtbot, window)


class VanishingArchive:
    """Real archive that, at read number `at`, first removes the file (then reads)."""

    def __init__(self, inner: FileArchive, at: int, victim: Path) -> None:
        self._inner = inner
        self._at = at
        self._victim = victim
        self.reads = 0

    def read(self, file: ArchivedFile, max_bytes: int) -> bytes:
        self.reads += 1
        if self.reads == self._at:
            self._victim.unlink()
        return self._inner.read(file, max_bytes)

    def verify(self, file: ArchivedFile) -> None:
        self._inner.verify(file)


def test_a_file_that_vanishes_between_the_index_and_the_read_is_a_reason_not_a_crash(
    tmp_path: Path,
) -> None:
    profile = profile_with_mixed(tmp_path)
    session = profile.session
    try:
        first: ActivityRecord = session.activities.list_by_start()[0]
        victim = profile.folder / PROFILE_ARCHIVE_DIR / first.archived.relative_path
        archive = VanishingArchive(profile.archive, at=1, victim=victim)
        detail = GetActivityDetail(
            session.activities,
            archive,  # type: ignore[arg-type]
            session.physiology,
            decode_stored_activity,
            decode_stored_track,
            timezone=ZoneInfo("Europe/Rome"),
        )

        result = detail(first.identity)

        assert isinstance(result, ExcludedActivity)
        assert result.reason is ExclusionReason.FILE_MISSING
        assert not victim.exists()
    finally:
        profile.close()


def test_files_that_vanish_during_the_calculation_exclude_only_their_activity(
    tmp_path: Path,
) -> None:
    profile = profile_with_mixed(tmp_path)
    session = profile.session
    records = session.activities.list_by_start()
    try:
        # While the second activity is being read the file of the fourth disappears.
        later = profile.folder / PROFILE_ARCHIVE_DIR / records[3].archived.relative_path
        archive = VanishingArchive(profile.archive, at=2, victim=later)
        overview = ComputeProfileOverview(
            session.activities,
            archive,  # type: ignore[arg-type]
            session.physiology,
            session.load_cache,
            decode_stored_activity,
            timezone=ZoneInfo("Europe/Rome"),
            today=lambda: date(2026, 7, 1),
        ).execute()

        assert [e.identity for e in overview.excluded] == [records[3].identity]
        assert overview.excluded[0].reason is ExclusionReason.FILE_MISSING
        assert len(overview.activities) == len(records) - 1
        assert len(overview.pmc) > 0  # the PMC is calculated with the others
    finally:
        profile.close()


# --- Weights changed during a synchronisation --------------------------------------------------
