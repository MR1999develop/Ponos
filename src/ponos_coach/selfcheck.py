# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Self-check of an installation: `ponos-coach --self-check REPORT`.

The smoke test of the packaged executable (`nox -s dist`) runs it with the offscreen Qt platform:
it proves that what the app needs at run time is in the package, not just in the source tree
(the food table, the translations, the web resources, the time zone data, the web engine
process and the modules loaded on demand). Each check is independent; the report has one line
per check, `ok <name>` or `FAIL <name>: <cause>`, and the exit code is 0 only if all pass.
Nothing is read from the profiles and nothing is written except the report.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEventLoop, QTimer, QTranslator
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWidgets import QApplication

from ponos_coach.nutrition.food_table import FoodTable
from ponos_coach.ui.translations import (
    APP_CATALOG,
    QT_CATALOG,
    app_translations_dir,
    qt_translations_dir,
)
from ponos_coach.ui.web_resources import resource_for_path

WEB_ENGINE_TIMEOUT_MS: Final = 60_000
_WEB_PATHS: Final = ("/chart.html", "/chart.js", "/plotly.min.js")
_PLOTLY_MARKER: Final = b"Plotly"
_LOADED_ON_DEMAND: Final = (
    "ponos_coach.garmin.connect",
    "ponos_coach.platform.token_store",
    "ponos_coach.platform.notifier",
    "fitdecode",
    "defusedxml.ElementTree",
    "keyring",
    "desktop_notifier",
)
_MIN_FOODS: Final = 40


class SelfCheckError(Exception):
    """A check failed; the message is the cause shown in the report."""


@dataclass(frozen=True, slots=True)
class CheckResult:
    """The outcome of one check: `problem` is `None` when it passed."""

    name: str
    problem: str | None


def _check_food_table(read_food_table: Callable[[], FoodTable]) -> None:
    if len(read_food_table().foods) < _MIN_FOODS:
        raise SelfCheckError("the food table has too few foods")


def _check_translations() -> None:
    for catalog, folder in (
        (APP_CATALOG, app_translations_dir()),
        (QT_CATALOG, qt_translations_dir()),
    ):
        if not QTranslator().load(str(folder / catalog)):
            raise SelfCheckError(f"{catalog} is missing or unreadable")


def _check_web_resources() -> None:
    for path in _WEB_PATHS:
        resource = resource_for_path(path)
        if resource is None or not resource.content:
            raise SelfCheckError(f"{path} is missing or empty")
    plotly = resource_for_path("/plotly.min.js")
    if plotly is None or _PLOTLY_MARKER not in plotly.content:
        raise SelfCheckError("plotly.min.js is not Plotly")


def _check_time_zones() -> None:
    ZoneInfo("Europe/Rome")  # raises ZoneInfoNotFoundError without the tz database


def _check_imports() -> None:
    for module in _LOADED_ON_DEMAND:
        importlib.import_module(module)


def _check_web_engine() -> None:
    """The web engine starts its process and loads a page, within `WEB_ENGINE_TIMEOUT_MS`."""
    app = QApplication.instance()
    if app is None:
        raise SelfCheckError("no QApplication")
    page = QWebEnginePage()
    loop = QEventLoop()
    outcome: list[bool] = []

    def finished(ok: bool) -> None:
        outcome.append(ok)
        loop.quit()

    page.loadFinished.connect(finished)
    QTimer.singleShot(WEB_ENGINE_TIMEOUT_MS, loop.quit)
    page.setHtml("<html><body>ok</body></html>")
    loop.exec()
    page.deleteLater()
    if outcome != [True]:
        raise SelfCheckError("the web engine did not load a page in time")


def run_checks(checks: Sequence[tuple[str, Callable[[], None]]]) -> list[CheckResult]:
    """Runs every check; a failure of one never stops the others."""
    results: list[CheckResult] = []
    for name, check in checks:
        try:
            check()
        except Exception as exc:
            results.append(CheckResult(name, f"{type(exc).__name__}: {exc}"))
        else:
            results.append(CheckResult(name, None))
    return results


def format_report(results: Sequence[CheckResult]) -> str:
    """One line per check."""
    return "".join(
        f"ok {r.name}\n" if r.problem is None else f"FAIL {r.name}: {r.problem}\n" for r in results
    )


def run_self_check(report: Path, read_food_table: Callable[[], FoodTable]) -> int:
    """Runs the checks and writes the report to the new file `report`.

    Needs a `QApplication`. Returns 0 if every check passed, 1 otherwise.

    Raises:
        FileExistsError: `report` exists (a report never overwrites a file).
        OSError: the report cannot be written.
    """
    results = run_checks(
        [
            ("food table", lambda: _check_food_table(read_food_table)),
            ("translations", _check_translations),
            ("web resources", _check_web_resources),
            ("time zones", _check_time_zones),
            ("modules loaded on demand", _check_imports),
            ("web engine", _check_web_engine),
        ]
    )
    with report.open("x", encoding="utf-8") as handle:
        handle.write(format_report(results))
    return 0 if all(r.problem is None for r in results) else 1
