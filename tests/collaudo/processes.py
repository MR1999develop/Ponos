# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Test subprocesses: start-up, line-based text dialogue and death without cleanup."""

from __future__ import annotations

import contextlib
import ctypes
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Self

WORKERS = Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_workers.py"
LINE_TIMEOUT_S = 120  # common cap on waiting for a line: it expires only if something is broken
# On Windows `os.kill` with `SIGTERM` is `TerminateProcess`: no cleanup, like a crash.
KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, not Literal: mypy does not discard a branch
_SYNCHRONIZE = 0x00100000
_WAIT_FOREVER_GUARD_MS = 60_000


def wait_for_process_exit(pid: int) -> None:
    """Waits until process `pid` has really terminated (on Windows `TerminateProcess` is
    asynchronous: while the process exists it still holds its locks and its files)."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(_SYNCHRONIZE, 0, pid)
        if handle:  # otherwise it is already gone
            try:
                kernel32.WaitForSingleObject(handle, _WAIT_FOREVER_GUARD_MS)
            finally:
                kernel32.CloseHandle(handle)


class Worker:
    """A `collaudo_workers.py` process that is talked to line by line.

    On Windows `sys.executable` of a virtual environment is a launcher that starts
    the real interpreter as a child: the interpreter is killed (`pid`, announced by the process)
    and then the launcher.
    """

    def __init__(self, *arguments: str | Path | int) -> None:
        self._process = subprocess.Popen(  # noqa: S603 - interprete e script fissi
            [sys.executable, "-I", str(WORKERS), *map(str, arguments)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.pid = 0
        self._lines_seen: list[str] = []

    def read_line(self) -> str:
        """Next output line (empty if the process has ended or does not answer)."""
        assert self._process.stdout is not None
        stdout = self._process.stdout
        lines: list[str] = []
        reader = threading.Thread(target=lambda: lines.append(stdout.readline()), daemon=True)
        reader.start()
        reader.join(LINE_TIMEOUT_S)
        line = lines[0].strip() if lines else ""
        self._lines_seen.append(line)
        return line

    def expect_ready(self) -> None:
        fields = self.read_line().split()
        assert fields[:1] == ["ready"], f"the process is not ready: {self._lines_seen}"
        self.pid = int(fields[1])

    def read_until(self, prefix: str) -> str:
        """Reads lines until one that starts with `prefix`; fails if the process ends."""
        while True:
            line = self.read_line()
            assert line, f"the process ended before '{prefix}': {self._lines_seen}"
            if line.startswith(prefix):
                return line

    def send(self, line: str) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()

    def kill(self) -> None:
        """Kills the interpreter with no cleanup at all and waits for the process to end."""
        if self.pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(self.pid, KILL_SIGNAL)
            wait_for_process_exit(self.pid)
        self._process.kill()
        self._process.wait(timeout=LINE_TIMEOUT_S)

    def wait(self) -> int:
        return self._process.wait(timeout=LINE_TIMEOUT_S)

    def close(self) -> None:
        self.kill()
        for stream in (self._process.stdin, self._process.stdout):
            if stream is not None:
                with contextlib.suppress(OSError):
                    stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


@contextmanager
def workers(*commands: Sequence[str | Path | int]) -> Iterator[list[Worker]]:
    """Starts one process per command; kills them all at the end."""
    started: list[Worker] = []
    try:
        for command in commands:
            started.append(Worker(*command))  # noqa: PERF401 - each one registers itself at once
        yield started
    finally:
        for worker in started:
            worker.close()
