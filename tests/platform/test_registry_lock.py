# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Profile list shared by two instances: every write is protected by a system lock."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.platform.profile_registry import (
    REGISTRY_LOCK_NAME,
    REGISTRY_LOCK_TIMEOUT_S,
    JsonProfileRegistry,
    RegistryBusyError,
)
from ponos_coach.profiles.ports import ProfileRegistryError
from tests.builders import IMPORTED_AT, registry_entry

_HOLDER_TIMEOUT_S = 60
_SHORT_TIMEOUT_S = 0.3
_HOLDER_SCRIPT = """
import os
import sys
from pathlib import Path
from ponos_coach.platform.file_lock import acquire_file_lock

lock = acquire_file_lock(Path(sys.argv[1]))
print("locked", os.getpid(), flush=True)
sys.stdin.read()  # stays alive (and holds the lock) until the test kills it
"""
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _read_first_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    stdout = process.stdout
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(stdout.readline()), daemon=True)
    reader.start()
    reader.join(_HOLDER_TIMEOUT_S)
    return lines[0] if lines else ""


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "config"
    directory.mkdir()
    return directory


@pytest.fixture
def lock_holder(config_dir: Path) -> Iterator[int]:
    """A subprocess that holds the list's lock; always killed at the end. Gives its pid."""
    process = subprocess.Popen(  # noqa: S603 - fixed executable and script, path as argument
        [sys.executable, "-I", "-c", _HOLDER_SCRIPT, str(config_dir / REGISTRY_LOCK_NAME)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    pid = 0
    try:
        fields = _read_first_line(process).split()
        assert fields[:1] == ["locked"], "the subprocess did not take the lock"
        pid = int(fields[1])
        yield pid
    finally:
        if pid:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, _KILL_SIGNAL)
        process.kill()
        process.wait(timeout=_HOLDER_TIMEOUT_S)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def test_the_wait_for_the_lock_is_bounded_and_short() -> None:
    assert 0 < REGISTRY_LOCK_TIMEOUT_S <= 5


def test_a_write_waits_a_bounded_time_for_another_instance_then_fails_loudly(
    config_dir: Path, lock_holder: int
) -> None:
    registry = JsonProfileRegistry(config_dir, lock_timeout_s=_SHORT_TIMEOUT_S)
    entry = registry_entry(1)

    with pytest.raises(RegistryBusyError):
        registry.add(entry)

    assert isinstance(RegistryBusyError("x"), ProfileRegistryError)
    assert registry.list_entries() == []  # nothing written, and reading does not wait for the lock


@pytest.mark.parametrize("operation", ["add", "remove", "mark_used"])
def test_every_write_operation_is_protected(
    config_dir: Path, operation: str, lock_holder: int
) -> None:
    registry = JsonProfileRegistry(config_dir, lock_timeout_s=_SHORT_TIMEOUT_S)
    entry = registry_entry(1)
    operations = {
        "add": lambda: registry.add(entry),
        "remove": lambda: registry.remove(entry.profile_id),
        "mark_used": lambda: registry.mark_used(entry.profile_id, IMPORTED_AT),
    }

    with pytest.raises(RegistryBusyError):
        operations[operation]()


def test_the_lock_of_a_killed_instance_does_not_block_the_next_write(
    config_dir: Path, lock_holder: int
) -> None:
    registry = JsonProfileRegistry(config_dir, lock_timeout_s=_SHORT_TIMEOUT_S)
    os.kill(lock_holder, _KILL_SIGNAL)
    entry = registry_entry(1)

    # The system releases the lock when the process dies: at most we wait for its end.
    registry.add(entry)

    assert registry.list_entries() == [entry]


def test_the_lock_is_released_after_each_operation_even_when_it_fails(config_dir: Path) -> None:
    registry = JsonProfileRegistry(config_dir, lock_timeout_s=_SHORT_TIMEOUT_S)

    with pytest.raises(ProfileRegistryError):
        registry.remove(
            registry_entry(9).profile_id
        )  # it is not there: error after taking the lock
    registry.add(registry_entry(1))

    assert len(registry.list_entries()) == 1


def test_two_instances_in_one_process_serialise_their_writes(config_dir: Path) -> None:
    first = JsonProfileRegistry(config_dir)
    second = JsonProfileRegistry(config_dir)
    errors: list[BaseException] = []

    def write(registry: JsonProfileRegistry, numbers: range) -> None:
        try:
            for number in numbers:
                registry.add(registry_entry(number))
        except BaseException as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(first, range(1, 21))),
        threading.Thread(target=write, args=(second, range(21, 41))),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_HOLDER_TIMEOUT_S)

    assert errors == []
    assert len(first.list_entries()) == 40  # no change lost
