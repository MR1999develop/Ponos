# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Diagnostica: log su file con rotazione, formatter sicuro e gestore di ultima istanza.

Il formatter è l'unico punto in cui un log diventa testo: di un'eccezione scrive solo il tipo e
la catena dei tipi (mai il testo, che può contenere percorsi o dati dell'utente, né le righe del
traceback) e neutralizza i caratteri di controllo, così un nome di file ostile non può
fingere una riga di log. Il Composition Root chiama `configure_logging` e
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
LOG_BACKUP_COUNT = 3  # al massimo (1 + 3) file da 1 MB nella cartella dei log
MAX_CHAIN_LENGTH = 8  # tipi della catena di un'eccezione che si scrivono
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}
_HANDLER_MARK = "_ponos_coach_handler"


def neutralize(text: str) -> str:
    """Sostituisce i caratteri di controllo (a-capo compresi) con la loro forma scritta."""
    return _CONTROL.sub(lambda m: _ESCAPES.get(m[0], f"\\x{ord(m[0]):02x}"), text)


def _type_name(kind: type[BaseException]) -> str:
    if kind.__module__ == "builtins":
        return kind.__qualname__
    return f"{kind.__module__}.{kind.__qualname__}"


def exception_chain(error: BaseException) -> str:
    """I tipi di un'eccezione e delle sue cause, dal più esterno: `A <- B <- C`. Mai i testi."""
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
    """Formatter dei log: messaggio neutralizzato, eccezioni solo come catena di tipi."""

    converter = staticmethod(time.gmtime)

    def __init__(self) -> None:
        super().__init__(_FORMAT, _DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        """La riga di log di un record (una sola riga, senza traceback)."""
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
    """Scrive i log di `ponos_coach` su un file con rotazione in `log_dir`.

    Restituisce il file, o `None` se la cartella non si può usare: allora i log vanno su
    stderr e l'app parte lo stesso. Si può chiamare più volte (sostituisce il gestore).
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
    """Porta nel thread della UI la notizia di un errore non gestito, da qualunque thread."""

    occurred = Signal()

    def notify(self) -> None:
        """Si può chiamare da ogni thread: il segnale arriva nel thread della UI."""
        self.occurred.emit()


def install_exception_hooks(notify: Callable[[], None]) -> None:
    """Gestore di ultima istanza: registra l'errore non gestito e avvisa l'utente.

    Vale per il thread principale (`sys.excepthook`, usato da Qt per le eccezioni negli
    slot) e per gli altri thread (`threading.excepthook`). Si registrano solo i tipi.
    """
    logger = logging.getLogger(LOGGER_NAME)

    def report(where: str, exc_type: type[BaseException], exc_value: BaseException | None) -> None:
        chain = exception_chain(exc_value) if exc_value is not None else _type_name(exc_type)
        logger.critical("Unhandled exception in %s: %s", where, chain)
        try:
            notify()
        except Exception as error:  # il gestore di ultima istanza non deve fallire a sua volta
            logger.error(  # noqa: TRY400 - il testo dell'eccezione non si registra
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
