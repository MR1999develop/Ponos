# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Measures MapLibre GL JS and Leaflet in a `QWebEngineView` with the app's network policy.

Usage:
    python run_benchmark.py one <maplibre|leaflet> <starts|tracks|visible> [operations]
    python run_benchmark.py all [operations]     # every combination, one process each

Each scenario runs in a new process (clean memory). The page is served from the `ponos://app/`
scheme as in the app; there is no network request. The result is written to stdout as one JSON
line and, with `all`, to `results-<date>.json`. Memory: working set of the QtWebEngine child
processes (renderer, GPU) and of the main process, read through PowerShell.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT_FILES = {
    "page_maplibre.html",
    "page_leaflet.html",
    "bench_common.js",
    "bench_maplibre.js",
    "bench_leaflet.js",
    "data.js",
}
MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
SCENARIO_TIMEOUT_S = 900
LIBS = ("maplibre", "leaflet")
MODES = ("starts", "visible", "tracks")
RESULT_PREFIX = "PONOS_RESULT "
LOG_PREFIXES = ("PONOS_ERROR", "PONOS_LOG")
MAX_LOGGED_CHARS = 300


def served_file(path: str) -> Path | None:
    """File served for a URL path: closed list (pages, scripts, vendor), never `..`."""
    relative = path.lstrip("/")
    if relative in ROOT_FILES:
        return HERE / relative
    if relative.startswith("vendor/") and ".." not in relative and "\\" not in relative:
        candidate = (HERE / relative).resolve()
        in_vendor = candidate.is_relative_to(HERE / "vendor")
        if candidate.is_file() and in_vendor and candidate.suffix in MIME:
            return candidate
    return None


def memory_mb() -> dict[str, float]:
    """Current and peak working set (MB) of this process and its children, via PowerShell."""
    pid = os.getpid()
    script = (
        f"$ids=@({pid}) + (Get-CimInstance Win32_Process -Filter 'ParentProcessId={pid}'"
        " | ForEach-Object { $_.ProcessId });"
        " $p=Get-Process -Id $ids -ErrorAction SilentlyContinue;"
        " $w=($p | Measure-Object WorkingSet64 -Sum).Sum;"
        " $k=($p | Measure-Object PeakWorkingSet64 -Sum).Sum;"
        ' "$w $k"'
    )
    out = subprocess.run(  # noqa: S603
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    ).stdout.split()
    return {"working_set_mb": float(out[0]) / 1e6, "peak_working_set_mb": float(out[1]) / 1e6}


def handle_console_message(message: str, outcome: dict[str, object]) -> bool:
    """Records a page message; returns True when it was the final result."""
    if message.startswith(RESULT_PREFIX):
        outcome.update(json.loads(message[len(RESULT_PREFIX) :]))
        return True
    if message.startswith(LOG_PREFIXES):
        print(message[:MAX_LOGGED_CHARS], file=sys.stderr, flush=True)
        if message.startswith("PONOS_ERROR"):
            errors = outcome.setdefault("errors", [])
            if isinstance(errors, list):
                errors.append(message[:MAX_LOGGED_CHARS])
    return False


def run_one(lib: str, mode: str, operations: int) -> dict[str, object]:
    # Imported here: `all` does not need Qt.
    from PySide6.QtCore import QBuffer, QByteArray, QTimer, QUrl  # noqa: PLC0415
    from PySide6.QtWebEngineCore import (  # noqa: PLC0415
        QWebEnginePage,
        QWebEngineProfile,
        QWebEngineUrlRequestJob,
        QWebEngineUrlSchemeHandler,
    )
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: PLC0415
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    from ponos_coach.ui.web_policy import register_app_scheme  # noqa: PLC0415

    class Handler(QWebEngineUrlSchemeHandler):
        def requestStarted(self, job: QWebEngineUrlRequestJob) -> None:  # noqa: N802
            url = job.requestUrl()
            file = served_file(url.path()) if url.host() == "app" else None
            if file is None:
                job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
                return
            buffer = QBuffer(job)
            buffer.setData(QByteArray(file.read_bytes()))
            job.reply(MIME[file.suffix].encode(), buffer)

    outcome: dict[str, object] = {}

    def finish() -> None:
        outcome["memory"] = memory_mb()
        app.quit()

    class Page(QWebEnginePage):
        def javaScriptConsoleMessage(  # noqa: N802
            self,
            level: QWebEnginePage.JavaScriptConsoleMessageLevel,  # noqa: ARG002
            message: str,
            line: int,  # noqa: ARG002
            source: str,  # noqa: ARG002
        ) -> None:
            if handle_console_message(message, outcome):
                QTimer.singleShot(500, finish)

    register_app_scheme()
    app = QApplication(sys.argv)
    profile = QWebEngineProfile()  # off the record: no cache or cookies on disk
    handler = Handler()
    profile.installUrlSchemeHandler(b"ponos", handler)
    page = Page(profile)
    view = QWebEngineView()
    view.setPage(page)
    view.resize(1280, 800)
    view.show()
    started = time.perf_counter()
    view.load(QUrl(f"ponos://app/page_{lib}.html?mode={mode}&ops={operations}"))
    QTimer.singleShot(
        SCENARIO_TIMEOUT_S * 1000, lambda: (outcome.setdefault("timeout", True), app.quit())
    )
    app.exec()
    outcome["wall_s"] = time.perf_counter() - started
    return outcome


def run_all(operations: int) -> None:
    results = []
    for lib in LIBS:
        for mode in MODES:
            print(f"== {lib} / {mode}", flush=True)
            proc = subprocess.run(  # noqa: S603
                [sys.executable, "-I", str(Path(__file__)), "one", lib, mode, str(operations)],
                capture_output=True,
                text=True,
                timeout=SCENARIO_TIMEOUT_S + 120,
                check=False,
            )
            line = next((x for x in proc.stdout.splitlines() if x.startswith("{")), None)
            outcome = json.loads(line) if line else {"failed": proc.stderr[-300:]}
            results.append({"lib": lib, "mode": mode, **outcome})
            print(results[-1], flush=True)
    results_path = HERE / f"results-{time.strftime('%Y%m%d')}.json"
    environment = {
        "python": platform.python_version(),
        "os": platform.platform(),
        "cpus": os.cpu_count(),
    }
    results_path.write_text(
        json.dumps({"environment": environment, "results": results}, indent=1), encoding="utf-8"
    )


def main() -> None:
    args = sys.argv[1:]
    if args[:1] == ["one"]:
        operations = int(args[3]) if len(args) > 3 else 40
        print(json.dumps(run_one(args[1], args[2], operations)))
    elif args[:1] == ["all"]:
        run_all(int(args[1]) if len(args) > 1 else 40)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
