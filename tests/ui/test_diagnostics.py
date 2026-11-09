# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""File logging with rotation, safe formatter and last-resort handler (R5, D-F1-05)."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import platformdirs
import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from ponos_coach import __main__ as entry_point
from ponos_coach.ui.diagnostics import (
    LOG_BACKUP_COUNT,
    LOG_FILE_NAME,
    LOG_MAX_BYTES,
    ErrorNotifier,
    SafeFormatter,
    configure_logging,
    exception_chain,
    install_exception_hooks,
    neutralize,
)
from ponos_coach.ui.main_window import MainWindow
from ponos_coach.ui.translations import Language, TranslationReport
from tests.ui.workspace_support import make_settings

USER_FOLDER = "C:/Users/someone/Segreto_Di_Prova"


def record_of(message: str, *args: object, exc: BaseException | None = None) -> logging.LogRecord:
    exc_info = (type(exc), exc, exc.__traceback__) if exc is not None else None
    return logging.LogRecord(
        "ponos_coach.test", logging.ERROR, __file__, 1, message, args, exc_info
    )


@pytest.fixture(autouse=True)
def restore_global_state() -> Iterator[None]:
    """Handlers and logs are global process state: each test leaves them as it found them."""
    logger = logging.getLogger("ponos_coach")
    handlers, level = list(logger.handlers), logger.level
    excepthook, thread_hook = sys.excepthook, threading.excepthook
    yield
    for handler in [h for h in logger.handlers if h not in handlers]:
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(level)
    sys.excepthook, threading.excepthook = excepthook, thread_hook


def test_control_characters_become_visible_escapes_so_no_line_can_be_forged() -> None:
    line = SafeFormatter().format(record_of("folder %s", "x\nERROR ponos_coach.fake riga\r\x1b[0m"))

    assert "\n" not in line
    assert "\r" not in line
    assert "\x1b" not in line
    assert "x\\nERROR ponos_coach.fake riga\\r\\x1b[0m" in line


def test_neutralize_leaves_ordinary_text_and_unicode_alone() -> None:
    assert neutralize("Atleta è già lì: 5 km") == "Atleta è già lì: 5 km"
    assert neutralize("a" + chr(0x2028) + "b") == r"a\x2028b"


def test_an_exception_is_written_as_the_chain_of_its_types_never_its_text_or_traceback() -> None:
    try:
        try:
            raise FileNotFoundError(f"missing {USER_FOLDER}")
        except OSError as inner:
            raise ValueError(f"bad {USER_FOLDER}") from inner
    except ValueError as error:
        line = SafeFormatter().format(record_of("Job failed", exc=error))

    assert line.endswith("Job failed [ValueError <- FileNotFoundError]")
    assert USER_FOLDER not in line
    assert "Traceback" not in line
    assert "\n" not in line


def test_the_chain_follows_implicit_context_and_stops_on_a_loop() -> None:
    class OuterError(Exception):
        pass

    try:
        try:
            raise KeyError("k")  # noqa: TRY301 - the raise is needed for the chain
        except KeyError:
            raise OuterError("o") from None
    except OuterError as hidden:
        assert exception_chain(hidden).endswith(".OuterError")  # `from None` hides the context
        assert "KeyError" not in exception_chain(hidden)
    loop = RuntimeError("a")
    loop.__cause__ = loop
    assert exception_chain(loop) == "RuntimeError"


def test_logging_goes_to_a_rotating_file_with_a_cap_in_the_log_folder(tmp_path: Path) -> None:
    folder = tmp_path / "logs"

    log_file = configure_logging(folder, version="9.9")

    assert log_file == folder / LOG_FILE_NAME
    handlers = [
        h
        for h in logging.getLogger("ponos_coach").handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert (handlers[0].maxBytes, handlers[0].backupCount) == (LOG_MAX_BYTES, LOG_BACKUP_COUNT)
    logging.getLogger("ponos_coach.ui.test").error("Something %s", "failed\nERROR fake")
    handlers[0].flush()
    text = log_file.read_text(encoding="utf-8")
    assert "Ponos Coach 9.9 started" in text
    assert "ERROR ponos_coach.ui.test Something failed\\nERROR fake" in text
    assert not [line for line in text.splitlines() if line.startswith("ERROR")]  # date first


def test_configuring_twice_replaces_the_handler_instead_of_duplicating_it(tmp_path: Path) -> None:
    configure_logging(tmp_path / "a", version="1")
    configure_logging(tmp_path / "b", version="1")

    ours = [
        h
        for h in logging.getLogger("ponos_coach").handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]
    assert len(ours) == 1
    assert Path(ours[0].baseFilename).parent == tmp_path / "b"


def test_an_unusable_log_folder_does_not_stop_the_app_and_logs_go_to_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")

    log_file = configure_logging(blocker / "logs", version="1")

    assert log_file is None
    assert "The log folder cannot be used" in capsys.readouterr().err


def test_the_last_resort_handler_logs_only_types_and_tells_the_user(
    tmp_path: Path, qtbot: QtBot
) -> None:
    log_file = configure_logging(tmp_path, version="1")
    assert log_file is not None
    notifier = ErrorNotifier()
    install_exception_hooks(notifier.notify)

    def boom() -> None:
        raise ValueError(f"cannot open {USER_FOLDER}")

    thread = threading.Thread(target=boom)
    with qtbot.waitSignal(notifier.occurred, timeout=10_000):
        thread.start()
        thread.join()
    for handler in logging.getLogger("ponos_coach").handlers:
        handler.flush()

    text = log_file.read_text(encoding="utf-8")
    assert "CRITICAL ponos_coach Unhandled exception in a worker thread: ValueError" in text
    assert USER_FOLDER not in text


def test_the_main_thread_hook_logs_and_notifies_but_lets_ctrl_c_through(tmp_path: Path) -> None:
    log_file = configure_logging(tmp_path, version="1")
    assert log_file is not None
    calls: list[str] = []
    install_exception_hooks(lambda: calls.append("notified"))

    try:
        raise OSError(USER_FOLDER)  # noqa: TRY301 - the raise is needed by the test
    except OSError:
        sys.excepthook(*sys.exc_info())
    for handler in logging.getLogger("ponos_coach").handlers:
        handler.flush()

    assert calls == ["notified"]
    text = log_file.read_text(encoding="utf-8")
    assert "Unhandled exception in the main thread: OSError" in text
    assert USER_FOLDER not in text


def test_a_failing_notification_is_logged_and_does_not_raise(tmp_path: Path) -> None:
    log_file = configure_logging(tmp_path, version="1")
    assert log_file is not None

    def failing() -> None:
        raise RuntimeError(USER_FOLDER)

    install_exception_hooks(failing)
    try:
        raise ValueError("x")  # noqa: TRY301 - the raise is needed by the test
    except ValueError:
        sys.excepthook(*sys.exc_info())
    for handler in logging.getLogger("ponos_coach").handlers:
        handler.flush()

    text = log_file.read_text(encoding="utf-8")
    assert "Could not tell the user about an error: RuntimeError" in text
    assert USER_FOLDER not in text


def test_the_composition_root_logs_to_the_user_log_folder_and_installs_the_hooks(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    logs = tmp_path / "logs"
    seen: dict[str, object] = {}

    def fake_exec() -> int:
        seen["hooked"] = sys.excepthook is not sys.__excepthook__
        windows = [w for w in app.topLevelWidgets() if isinstance(w, MainWindow)]
        assert len(windows) == 1
        window = windows[0]
        qtbot.addWidget(window)
        seen["log_action_enabled"] = window.log_action.isEnabled()
        window.close()
        qtbot.waitUntil(lambda: window.is_closed, timeout=60_000)
        return 0

    monkeypatch.setattr(entry_point, "QApplication", lambda _argv: app)
    monkeypatch.setattr(
        entry_point,
        "setup_translations",
        lambda *_a, **_k: TranslationReport(Language.ENGLISH, False, False, ()),  # no language
    )
    monkeypatch.setattr(entry_point, "QSettings", lambda: make_settings(tmp_path))
    monkeypatch.setattr(app, "exec", fake_exec, raising=False)
    monkeypatch.setattr(platformdirs, "user_log_dir", lambda *_a, **_k: str(logs), raising=True)
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda *_a, **_k: str(tmp_path / "config"))
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda *_a, **_k: str(tmp_path / "data"))
    # The map's tile cache too: never the user's own cache folder.
    monkeypatch.setattr(platformdirs, "user_cache_dir", lambda *_a, **_k: str(tmp_path / "cache"))

    assert entry_point.main([]) == 0

    assert seen == {"hooked": True, "log_action_enabled": True}
    text = (logs / LOG_FILE_NAME).read_text(encoding="utf-8")
    assert "Ponos Coach" in text
    assert "started (Python" in text
