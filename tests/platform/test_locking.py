# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-DATI-04: one process per profile; the lock of a dead process blocks nobody."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import PROFILE_LOCK_NAME, FolderKind
from ponos_coach.profiles.ports import FolderUnavailableError, ProfileInUseError

_HOLDER_TIMEOUT_S = 60
_HOLDER_SCRIPT = """
import os
import sys
from pathlib import Path
from ponos_coach.platform.profile_folders import LocalProfileFolders
from ponos_coach.profiles.domain import FolderKind

lock = LocalProfileFolders(lambda _folder: FolderKind.LOCAL).acquire_lock(Path(sys.argv[1]))
print("locked", os.getpid(), flush=True)
sys.stdin.read()  # stays alive (and holds the lock) until the test kills it
"""
# Always without cleanup: on Windows `os.kill`'s `SIGTERM` is `TerminateProcess`.
_KILL_SIGNAL = getattr(signal, "SIGKILL", signal.SIGTERM)


@dataclass(frozen=True)
class _Holder:
    """The process that holds the lock.

    On Windows `sys.executable` of a virtual environment is a launcher that starts the real
    interpreter as a child: the lock lives in the interpreter (`pid`), not in the launcher.
    """

    launcher: subprocess.Popen[str]
    pid: int

    def crash(self) -> None:
        """Terminate the interpreter with no cleanup, like a crash, and wait for the end."""
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(self.pid, _KILL_SIGNAL)
        self.launcher.wait(timeout=_HOLDER_TIMEOUT_S)


@pytest.fixture
def folders() -> LocalProfileFolders:
    return LocalProfileFolders(lambda _folder: FolderKind.LOCAL)


@pytest.fixture
def profile_folder(tmp_path: Path, folders: LocalProfileFolders) -> Path:
    folder = tmp_path / "profilo"
    folders.create_private_folder(folder)
    return folder


def _read_first_line(process: subprocess.Popen[str]) -> str:
    """First line of the subprocess output, with a maximum time."""
    assert process.stdout is not None
    stdout = process.stdout
    lines: list[str] = []
    reader = threading.Thread(target=lambda: lines.append(stdout.readline()), daemon=True)
    reader.start()
    reader.join(_HOLDER_TIMEOUT_S)
    return lines[0] if lines else ""


@pytest.fixture
def lock_holder(profile_folder: Path) -> Iterator[_Holder]:
    """A subprocess that holds the profile lock; always killed at the end."""
    process = subprocess.Popen(  # noqa: S603 - fixed executable and script, path as argument
        [sys.executable, "-I", "-c", _HOLDER_SCRIPT, str(profile_folder)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    holder: _Holder | None = None
    try:
        fields = _read_first_line(process).split()
        assert fields[:1] == ["locked"], "the subprocess did not take the lock"
        holder = _Holder(process, int(fields[1]))
        yield holder
    finally:
        if holder is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(holder.pid, _KILL_SIGNAL)
        process.kill()
        process.wait(timeout=_HOLDER_TIMEOUT_S)
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


def test_second_instance_on_the_same_profile_is_refused_while_a_process_holds_it(
    folders: LocalProfileFolders, profile_folder: Path, lock_holder: _Holder
) -> None:
    with pytest.raises(ProfileInUseError):
        folders.acquire_lock(profile_folder)


def test_lock_left_by_a_killed_process_is_released_by_the_system(
    folders: LocalProfileFolders, profile_folder: Path, lock_holder: _Holder
) -> None:
    lock_holder.crash()  # no cleanup, no release()

    lock = folders.acquire_lock(profile_folder)

    lock.release()
    assert (profile_folder / PROFILE_LOCK_NAME).exists()  # the file stays: it is not the lock


def test_lock_is_refused_inside_the_same_process_too(
    folders: LocalProfileFolders, profile_folder: Path
) -> None:
    first = folders.acquire_lock(profile_folder)
    try:
        with pytest.raises(ProfileInUseError):
            folders.acquire_lock(profile_folder)
    finally:
        first.release()


def test_lock_file_that_is_a_symbolic_link_is_not_followed(
    folders: LocalProfileFolders, profile_folder: Path, tmp_path: Path
) -> None:
    target = tmp_path / "altrove"
    target.write_bytes(b"dati")
    try:
        (profile_folder / PROFILE_LOCK_NAME).symlink_to(target)
    except OSError:
        pytest.skip("symbolic links cannot be created here (privileges)")

    with pytest.raises(FolderUnavailableError):
        folders.acquire_lock(profile_folder)

    assert target.read_bytes() == b"dati"
