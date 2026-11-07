# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Diagnostics: rotating file log, safe formatter and last-resort handler.

The formatter is the only place where a log becomes text: of an exception it writes only the type
and
the chain of types (never the text, which may contain paths or user data, nor the lines of the
traceback) and neutralises control characters, so a hostile file name cannot
forge a log line. The Composition Root calls `configure_logging` and
`install_exception_hooks`.
"""

from __future__ import annotations

import copy
import logging
import logging.handlers
import re
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QObject, Signal

LOGGER_NAME = "ponos_coach"
LOG_FILE_NAME = "ponos-coach.log"
LOG_MAX_BYTES = 1_000_000
LOG_BACKUP_COUNT = 3  # at most (1 + 3) files of 1 MB in the log folder
MAX_CHAIN_LENGTH = 8  # types of an exception chain that are written
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_HANDLER_MARK = "_ponos_coach_handler"


def neutralize(text: str) -> str:
    """Replace control characters (newlines included) with their written form."""
    return _CONTROL.sub(lambda m: _ESCAPES.get(m[0], f"\\x{ord(m[0]):02x}"), text)


def _type_name(kind: type[BaseException]) -> str:
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def exception_chain(error: BaseException) -> str:
    """The types of an exception and its causes, outermost first: `A <- B <- C`. Never the texts."""
    names: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen and len(names) < MAX_CHAIN_LENGTH:
        seen.add(id(current))
        names.append(_type_name(type(current)))
        implicit = None if current.__suppress_context__ else current.__context__
        current = current.__cause__ or implicit
    return " <- ".join(names)


class SafeFormatter(logging.Formatter):
    """Log formatter: neutralised message, exceptions only as a chain of types."""

    converter = staticmethod(time.gmtime)

    def __init__(self) -> None:
        super().__init__(_FORMAT, _DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        """The log line of a record (a single line, without traceback)."""
        clone = copy.copy(record)
        clone.msg = neutralize(record.getMessage())
        clone.args = None
        clone.exc_info = None
        clone.exc_text = None
        clone.stack_info = None
        line = super().format(clone)
        if record.exc_info is not None and record.exc_info[1] is not None:
            line = f"{line} [{exception_chain(record.exc_info[1])}]"
        return line


def configure_logging(log_dir: Path, *, version: str) -> Path | None:
    """Write the `ponos_coach` logs to a rotating file in `log_dir`.

    Return the file, or `None` if the folder cannot be used: then logs go to
    stderr and the app starts anyway. Can be called several times (replaces the handler).
    """
    logger = logging.getLogger(LOGGER_NAME)
    for old in [h for h in logger.handlers if getattr(h, _HANDLER_MARK, False)]:
        logger.removeHandler(old)
        old.close()
    log_file: Path | None = log_dir / LOG_FILE_NAME
    handler: logging.Handler
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / LOG_FILE_NAME,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        log_file = None
        handler = logging.StreamHandler()
    handler.setFormatter(SafeFormatter())
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.info("Ponos Coach %s started (Python %s)", version, sys.version.split()[0])
    if log_file is None:
        logger.error("The log folder cannot be used: logging to the error output")
    return log_file


class ErrorNotifier(QObject):
    """Bring the news of an unhandled error to the UI thread, from any thread."""

    occurred = Signal()

    def notify(self) -> None:
        """Can be called from any thread: the signal arrives on the UI thread."""
        self.occurred.emit()


def install_exception_hooks(notify: Callable[[], None]) -> None:
    """Last-resort handler: log the unhandled error and notify the user.

    Applies to the main thread (`sys.excepthook`, used by Qt for exceptions in
    slots) and to the other threads (`threading.excepthook`). Only the types are logged.
    """
    logger = logging.getLogger(LOGGER_NAME)

    def report(where: str, exc_type: type[BaseException], exc_value: BaseException | None) -> None:
        chain = exception_chain(exc_value) if exc_value is not None else _type_name(exc_type)
        logger.critical("Unhandled exception in %s: %s", where, chain)
        try:
            notify()
        except Exception as error:  # the last-resort handler must not fail in turn
            logger.error(  # noqa: TRY400 - the exception's text is not logged
                "Could not tell the user about an error: %s", exception_chain(error)
            )

    def on_main(
        exc_type: type[BaseException], exc_value: BaseException, _tb: TracebackType | None
    ) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, _tb)
            return
        report("the main thread", exc_type, exc_value)

    def on_thread(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        report("a worker thread", args.exc_type, args.exc_value)

    sys.excepthook = on_main
    threading.excepthook = on_thread
