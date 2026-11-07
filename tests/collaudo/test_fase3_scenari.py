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

from ponos_coach.activities.decoding import decode_stored_activity
from ponos_coach.activities.domain import (
    ActivityPage,
    ActivityRecord,
    ArchivedFile,
    GarminActivityId,
    Sport,
)
from ponos_coach.load.activity_detail import GetActivityDetail
from ponos_coach.load.domain import SportWeight, default_weight
from ponos_coach.load.use_cases import (
    ComputeProfileOverview,
    ExcludedActivity,
    ExclusionReason,
    SetSportWeights,
)
from ponos_coach.profiles.domain import PROFILE_ARCHIVE_DIR
from ponos_coach.storage.file_archive import FileArchive
from ponos_coach.ui.overview_state import Ready
from tests.collaudo.fase3_support import profile_with_mixed
from tests.fakes.garmin import FakeActivitySource
from tests.ui.garmin_support import (
    remote_runs,
    submit_login,
    sync_of,
    wait_activities,
)
from tests.ui.test_activity_detail_window import open_detail
from tests.ui.test_garmin_window import window_with_profile
from tests.ui.test_profile_data_window import (
    PROFILE,
    click,
    open_weights_dialog,
    window_with_activities,
)
from tests.ui.workspace_support import GarminFakes, close_window, make_fakes, make_settings

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
        weights = open_weights_dialog(qtbot, window)
        weights.box(Sport.RUNNING).setValue(
            1.5
        )  # the fingerprint changes: every load is recalculated
        click(weights.save_button)
        qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is None, timeout=WAIT_MS)
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
    for item in back.overview.activities:
        weight = 1.5 if item.sport is Sport.RUNNING else default_weight(item.sport).value
        assert item.load.load == pytest.approx(item.load.hr_tss * weight)
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
            session.sport_weights,
            decode_stored_activity,
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
            sport_weights=session.sport_weights,
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


class BlockFromSecondDownload:
    """Decorator of `ActivitySource`: the first activity is downloaded, from the second on it
    waits."""

    def __init__(self, inner: FakeActivitySource, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate
        self.downloads = 0
        self.blocked = threading.Event()

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        return self._inner.list_page(offset, limit)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        self.downloads += 1
        if self.downloads > 1:
            self.blocked.set()
            if not self._gate.wait(timeout=30):
                raise TimeoutError("gate never opened")
        return self._inner.download_original(activity_id, max_bytes)


@pytest.mark.parametrize("blocked_downloads", [1, 2])
def test_changing_the_sport_weights_during_a_synchronisation_gives_the_load_of_the_new_weights(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    gate: threading.Event,
    caplog: pytest.LogCaptureFixture,
    blocked_downloads: int,
) -> None:
    inner = FakeActivitySource(remote_runs(40, 41, 42))
    served = BlockFromSecondDownload(inner, gate)
    if blocked_downloads == 1:
        served.downloads = 1  # it blocks already at the first download: no activity downloaded yet
    fakes: GarminFakes = make_fakes(mfa_required=False, source=inner, served=served)
    window = window_with_profile(qtbot, tmp_path, settings, fakes)

    with caplog.at_level(logging.WARNING):
        submit_login(qtbot, window)
        qtbot.waitUntil(served.blocked.is_set, timeout=WAIT_MS)
        assert sync_of(window).is_syncing
        weights = open_weights_dialog(qtbot, window)
        weights.box(Sport.RUNNING).setValue(2.0)
        click(weights.save_button)
        qtbot.waitUntil(lambda: window.profile_dialogs.weights_dialog is None, timeout=WAIT_MS)
        assert sync_of(window).is_syncing  # the synchronisation was not touched
        with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
            gate.set()
        wait_activities(qtbot, window, 3)

    state = window.controller.overview_state
    assert isinstance(state, Ready)
    assert state.overview.excluded == ()
    for item in state.overview.activities:
        assert item.load.load == pytest.approx(item.load.hr_tss * 2.0)  # all with the new weight
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
    assert failure_boxes(window) == []
    close_window(qtbot, window)


def test_weights_are_not_persisted_by_a_cancelled_dialog_even_during_a_synchronisation(
    qtbot: QtBot,
    tmp_path: Path,
    settings: QSettings,
    gate: threading.Event,
) -> None:
    inner = FakeActivitySource(remote_runs(50, 51))
    served = BlockFromSecondDownload(inner, gate)
    window = window_with_profile(
        qtbot, tmp_path, settings, make_fakes(mfa_required=False, source=inner, served=served)
    )
    submit_login(qtbot, window)
    qtbot.waitUntil(served.blocked.is_set, timeout=WAIT_MS)
    weights = open_weights_dialog(qtbot, window)
    weights.box(Sport.RUNNING).setValue(4.0)

    weights.reject()
    with qtbot.waitSignal(sync_of(window).sync_finished, timeout=WAIT_MS):
        gate.set()
    wait_activities(qtbot, window, 2)

    state = window.controller.overview_state
    assert isinstance(state, Ready)
    for item in state.overview.activities:
        assert item.load.load == pytest.approx(item.load.hr_tss * 1.0)
    close_window(qtbot, window)


def test_a_set_of_weights_replaces_the_previous_one_even_when_saved_twice_in_a_row(
    tmp_path: Path,
) -> None:
    profile = profile_with_mixed(tmp_path)
    session = profile.session
    save = SetSportWeights(session.activities, session.sport_weights)
    try:
        save({Sport.RUNNING: SportWeight(2.0), Sport.CYCLING: SportWeight(0.1)})
        first = profile.overview()
        save({Sport.RUNNING: SportWeight(3.0)})  # cycling goes back to the default
        second = profile.overview()

        for before, after in zip(first.activities, second.activities, strict=True):
            assert after.load.hr_tss == before.load.hr_tss
            weight = {Sport.RUNNING: 3.0}.get(after.sport, default_weight(after.sport).value)
            assert after.load.load == pytest.approx(after.load.hr_tss * weight)
    finally:
        profile.close()
