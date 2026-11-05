# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-UI-01 e T-UI-02 (parziali): dal Composition Root, finestra, calcolo in un worker, stato."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QLabel, QListWidget
from pytestqt.qtbot import QtBot

from ponos_coach.__main__ import (
    PROVISIONAL_PHYSIOLOGY,
    compose_main_window,
    main,
    parse_command_line,
    resolve_user_timezone,
)
from ponos_coach.activities.ports import FitFileRef, FitFileSource, FitFileUnreadableError
from ponos_coach.load.use_cases import FitnessParameters
from ponos_coach.storage.fit_folder import FitFolder
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.overview_controller import OverviewController
from ponos_coach.ui.overview_state import Cancelled, Failed, Idle, Loading, OverviewState, Ready
from tests.fixtures.generators.history import generate_history

TODAY = date(2026, 2, 1)
HISTORY_START = date(2026, 1, 5)
WAIT_MS = 60_000


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)


def compose(settings: QSettings, argv: Sequence[str], zone_id: str = "UTC") -> MainWindow:
    return compose_main_window(
        parse_command_line(argv),
        settings=settings,
        system_timezone_id=zone_id,
        clock=lambda _zone: TODAY,
    )


def is_state(kind: type) -> Callable[[OverviewState], bool]:
    return lambda state: isinstance(state, kind)


class GatedSource:
    """Fake di `FitFileSource`: ogni lettura attende un cancello, con scadenza."""

    def __init__(self, inner: FitFileSource, gate: threading.Event) -> None:
        self._inner = inner
        self._gate = gate

    def list_files(self) -> Sequence[FitFileRef]:
        return self._inner.list_files()

    def read(self, ref: FitFileRef, max_bytes: int) -> bytes:
        if not self._gate.wait(timeout=20):
            raise FitFileUnreadableError(ref.name)
        return self._inner.read(ref, max_bytes)


def gated_window(
    qtbot: QtBot, settings: QSettings, gate: threading.Event
) -> tuple[MainWindow, OverviewController]:
    controller = OverviewController(
        lambda folder: GatedSource(FitFolder(folder), gate),
        FitnessParameters(PROVISIONAL_PHYSIOLOGY, ZoneInfo("UTC")),
        lambda: TODAY,
    )
    window = MainWindow(controller, settings)
    qtbot.addWidget(window)
    return window, controller


def test_window_started_with_fit_folder_shows_the_pmc(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    folder = tmp_path / "fit"
    paths = generate_history(folder, 21, 1, HISTORY_START)
    window = compose(settings, ["--fit-folder", str(folder)])
    qtbot.addWidget(window)

    with qtbot.waitSignal(
        window.controller.state_changed, timeout=WAIT_MS, check_params_cb=is_state(Ready)
    ):
        pass

    first_day = date.fromisoformat(paths[0].name[:10])
    expected_days = (TODAY - first_day).days + 1
    assert window.status_texts == (
        f"{len(paths)} activity(ies)",
        "0 rejected file(s)",
        f"{expected_days} day(s) in the chart",
    )
    figure = window.overview_page.chart.current_figure
    assert figure is not None
    assert [trace.name for trace in figure.data] == [
        "Fitness (CTL)",
        "Fatigue (ATL)",
        "Form (TSB)",
        "Daily load",
    ]
    assert len(figure.data[0].x) == expected_days
    assert window.overview_page.current_widget() is window.overview_page.chart
    assert window.windowTitle() == "fit - Ponos Coach"


def test_without_folder_the_empty_state_points_to_the_first_step(
    qtbot: QtBot, settings: QSettings
) -> None:
    window = compose(settings, [])
    qtbot.addWidget(window)

    page = window.overview_page
    assert isinstance(window.controller.state, Idle)
    assert page.current_widget() is page.state_page
    assert page.state_page.button.text() == "Open FIT folder..."
    assert all(label.isHidden() for label in window.findChildren(QLabel) if "(" in label.text())
    assert window.open_action.isEnabled()
    assert not window.stop_action.isEnabled()
    assert window.stop_action.toolTip()  # il motivo per cui e disabilitata


def test_unreadable_folder_becomes_a_message_with_a_remedy(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    window = compose(settings, ["--fit-folder", str(tmp_path / "missing")])
    qtbot.addWidget(window)

    with qtbot.waitSignal(
        window.controller.state_changed, timeout=WAIT_MS, check_params_cb=is_state(Failed)
    ):
        pass

    state = window.controller.state
    assert isinstance(state, Failed)
    assert state.message.title == "The folder could not be read"
    assert "missing" not in state.message.what + state.message.remedy
    assert window.overview_page.current_widget() is window.overview_page.state_page


def test_ui_stays_responsive_while_the_worker_is_busy_and_result_arrives_after(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    folder = tmp_path / "fit"
    generate_history(folder, 21, 2, HISTORY_START)
    gate = threading.Event()
    window, controller = gated_window(qtbot, settings, gate)

    window.open_folder(folder)
    qtbot.waitUntil(
        lambda: isinstance(controller.state, Loading) and controller.state.total > 0,
        timeout=WAIT_MS,
    )
    assert not window.open_action.isEnabled()
    assert window.open_action.toolTip()
    assert window.stop_action.isEnabled()

    started = time.monotonic()
    handled: list[float] = []
    QTimer.singleShot(0, lambda: handled.append(time.monotonic() - started))
    qtbot.waitUntil(lambda: bool(handled), timeout=1000)
    assert handled[0] < 0.1  # un evento si elabora entro 100 ms con il worker occupato

    gate.set()
    qtbot.waitUntil(lambda: isinstance(controller.state, Ready), timeout=WAIT_MS)
    assert window.open_action.isEnabled()


def test_stop_cancels_the_worker_and_the_user_can_open_a_folder_again(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    folder = tmp_path / "fit"
    generate_history(folder, 21, 3, HISTORY_START)
    gate = threading.Event()
    window, controller = gated_window(qtbot, settings, gate)
    window.open_folder(folder)
    qtbot.waitUntil(
        lambda: isinstance(controller.state, Loading) and controller.state.total > 0,
        timeout=WAIT_MS,
    )

    window.stop_action.trigger()
    gate.set()

    qtbot.waitUntil(lambda: isinstance(controller.state, Cancelled), timeout=WAIT_MS)
    assert window.open_action.isEnabled()
    assert window.overview_page.current_widget() is window.overview_page.state_page
    assert window.status_texts == ("", "", "")


def test_closing_the_window_waits_for_the_worker(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    folder = tmp_path / "fit"
    generate_history(folder, 14, 4, HISTORY_START)
    gate = threading.Event()
    window, controller = gated_window(qtbot, settings, gate)
    window.open_folder(folder)
    qtbot.waitUntil(
        lambda: isinstance(controller.state, Loading) and controller.state.total > 0,
        timeout=WAIT_MS,
    )
    release = threading.Timer(0.3, gate.set)
    release.start()

    assert window.close()  # bloccato qui finche il worker non e finito, poi rilascia tutto

    release.join()


def test_sidebar_has_overview_enabled_and_the_rest_disabled_with_a_reason(
    qtbot: QtBot, settings: QSettings
) -> None:
    window = compose(settings, [])
    qtbot.addWidget(window)

    sidebar = window.findChild(QListWidget, "sidebar")
    assert sidebar is not None
    items = [sidebar.item(row) for row in range(sidebar.count())]
    assert [item.text() for item in items][:2] == ["Overview", "Activities"]
    assert items[0].flags() & items[0].flags().ItemIsEnabled
    for item in items[1:]:
        assert not item.flags() & item.flags().ItemIsEnabled
        assert item.toolTip() == "Available in a later version"


def test_menus_carry_the_shortcuts(qtbot: QtBot, settings: QSettings) -> None:
    window = compose(settings, [])
    qtbot.addWidget(window)

    shortcuts = {
        action.text(): action.shortcut().toString()
        for action in window.findChildren(QAction)
        if not action.shortcut().isEmpty()
    }
    assert shortcuts == {
        "Open FIT folder...": QKeySequence("Ctrl+O").toString(),
        "Stop loading": QKeySequence("Esc").toString(),
        "Quit": QKeySequence("Ctrl+Q").toString(),
    }


def test_last_folder_and_window_geometry_are_remembered(
    qtbot: QtBot, tmp_path: Path, settings: QSettings
) -> None:
    folder = tmp_path / "fit"
    generate_history(folder, 7, 5, HISTORY_START)
    first = compose(settings, [])
    qtbot.addWidget(first)
    first.resize(800, 580)
    with qtbot.waitSignal(
        first.controller.state_changed, timeout=WAIT_MS, check_params_cb=is_state(Ready)
    ):
        first.open_folder(folder)
    first.close()

    second = compose(settings, [])
    qtbot.addWidget(second)

    assert settings.value("overview/lastFolder", "", type=str) == str(folder)
    assert (second.width(), second.height()) == (800, 580)


@pytest.mark.parametrize(
    ("zone_id", "expected_zone", "fell_back"),
    [("Europe/Rome", "Europe/Rome", False), ("Not/AZone", "UTC", True), ("", "UTC", True)],
)
def test_user_time_zone_falls_back_to_utc(
    zone_id: str, expected_zone: str, fell_back: bool
) -> None:
    zone, did_fall_back = resolve_user_timezone(zone_id)

    assert zone.key == expected_zone
    assert did_fall_back is fell_back


def test_unknown_time_zone_is_announced_in_the_status_bar(
    qtbot: QtBot, settings: QSettings
) -> None:
    window = compose(settings, [], zone_id="Not/AZone")
    qtbot.addWidget(window)

    texts = [label.text() for label in window.statusBar().findChildren(QLabel)]
    assert "Time zone not recognised: using UTC" in texts
    assert "Provisional heart-rate settings" in texts


def test_command_line_exit_codes(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.startswith("ponos-coach ")
    assert main(["--no-such-option"]) == 2
    assert "usage:" in capsys.readouterr().err
