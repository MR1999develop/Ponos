# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""T-SEC-07 (segreti) e T-SEC-08 (log) per la fase 2, sulla pila vera senza rete.

Token, password, email e codice MFA sono segnaposto noti. Dopo login, sincronizzazione, errori
con i segreti dentro i messaggi, crash (anche un `os._exit` vero) e logout, non devono comparire
nella cartella del profilo, nei temporanei, nella configurazione, nei log né negli oggetti
mostrabili. Sulle piattaforme con DPAPI si usa il portachiavi vero (file cifrato sotto `dati`).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from ponos_coach.activities.domain import SessionTokens
from ponos_coach.activities.sync import SyncMode
from ponos_coach.platform.token_store import DpapiTokenStore
from ponos_coach.profiles.ports import TokenStore
from ponos_coach.ui.diagnostics import configure_logging, install_exception_hooks
from ponos_coach.ui.sync_model import LoginEnd, SyncFinished
from ponos_coach.ui.sync_worker import Credentials
from tests.collaudo.fase2_support import (
    EMAIL,
    MFA_CODE,
    PASSWORD,
    Stack,
    build_stack,
    scripted_client,
)
from tests.fakes.tokens import InMemoryTokenStore
from tests.garmin.scripted import (
    PLACEHOLDER_TOKENS,
    SECRET_MARKERS,
    ScriptedClient,
    auth_error,
    http_error,
)

TOKEN_MARKER = "TOKEN-SEGNAPOSTO-0123456789abcdef"  # noqa: S105 - segnaposto
MARKERS = (TOKEN_MARKER, PASSWORD, EMAIL, MFA_CODE)
LEAKY = f"fallito per {EMAIL} con {PASSWORD} codice {MFA_CODE} token {TOKEN_MARKER}"
WORKER = (
    Path(__file__).resolve().parents[1] / "fixtures" / "generators" / "collaudo_fase2_worker.py"
)
_IS_WINDOWS: bool = sys.platform == "win32"  # bool, non Literal: mypy non scarta un ramo
windows_only = pytest.mark.skipif(not _IS_WINDOWS, reason="DPAPI di Windows")


def make_token_store(base: Path) -> TokenStore:
    """Il portachiavi vero dove c'è (DPAPI su Windows), altrimenti uno in memoria."""
    if _IS_WINDOWS:
        return DpapiTokenStore(base / "dati")
    return InMemoryTokenStore()


def files_with_secrets(root: Path, markers: tuple[str, ...] = MARKERS) -> list[str]:
    """I file sotto `root` che contengono un segnaposto, in UTF-8 o UTF-16 (LE)."""
    found: list[str] = []
    needles = [m.encode(enc) for m in markers for enc in ("utf-8", "utf-16-le")]
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except PermissionError:  # il file del lock, tenuto aperto in esclusiva dal profilo
            continue
        if any(needle in data for needle in needles):
            found.append(str(path.relative_to(root)))
    return found


@pytest.fixture
def isolated_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """I temporanei del processo finiscono in una cartella del test, che poi si scandisce."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    return temp


@pytest.fixture
def file_log(tmp_path: Path) -> Iterator[Path]:
    """Il log di `ponos_coach` su file, come nell'app, tolto alla fine."""
    log_file = configure_logging(tmp_path / "logs", version="test")
    assert log_file is not None
    yield log_file
    logger = logging.getLogger("ponos_coach")
    for handler in [h for h in logger.handlers if getattr(h, "_ponos_coach_handler", False)]:
        logger.removeHandler(handler)
        handler.close()


def logged_text(log_file: Path, caplog: pytest.LogCaptureFixture) -> str:
    """Tutto ciò che un log può mostrare: il file, i record (con eccezioni) e il formattato."""
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    lines = [formatter.format(record) for record in caplog.records]
    return log_file.read_text(encoding="utf-8") + "\n".join(lines)


def build(base: Path, client: ScriptedClient | None = None) -> Stack:
    stack = build_stack(base, client, tokens=make_token_store(base), linked=False)
    stack.factory.expected_mfa_code = MFA_CODE
    return stack


def link_with_mfa(stack: Stack) -> None:
    result = stack.worker.log_in(
        stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
    )
    assert result is LoginEnd.CONNECTED


# --- T-SEC-07 -------------------------------------------------------------------------------


def test_t_sec_07_after_login_sync_and_logout_no_secret_is_on_disk_or_in_the_logs(
    tmp_path: Path,
    isolated_temp: Path,
    file_log: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    stack = build(tmp_path, scripted_client(4))
    try:
        link_with_mfa(stack)
        first = stack.sync()
        assert isinstance(first, SyncFinished) and first.outcome.completed
        assert stack.tokens.load(stack.profile_id) is not None
        # con il token ancora nel portachiavi: non nella cartella del profilo né nei temporanei
        assert files_with_secrets(stack.folder) == []
        assert files_with_secrets(isolated_temp) == []
        assert files_with_secrets(tmp_path / "logs") == []
        assert stack.worker.log_out(stack.profile_id) is True
        assert stack.tokens.load(stack.profile_id) is None
    finally:
        stack.close()

    out, err = capfd.readouterr()
    assert files_with_secrets(tmp_path) == []  # nemmeno il portachiavi su disco, dopo il logout
    assert not [m for m in MARKERS if m in logged_text(file_log, caplog) + out + err]


@windows_only
def test_t_sec_07_the_keychain_file_is_encrypted_outside_the_profile_and_logout_removes_it(
    tmp_path: Path,
) -> None:
    stack = build(tmp_path, scripted_client(1))
    try:
        link_with_mfa(stack)
        secrets = list((tmp_path / "dati").rglob("*.bin"))

        assert len(secrets) == 1 and stack.folder not in secrets[0].parents
        assert files_with_secrets(tmp_path) == []  # cifrato: il segnaposto non e' in chiaro
        stack.worker.log_out(stack.profile_id)
        assert not list((tmp_path / "dati").rglob("*.bin"))
    finally:
        stack.close()


def test_t_sec_07_errors_that_carry_the_secrets_in_their_text_are_logged_by_type_only(
    tmp_path: Path,
    isolated_temp: Path,
    file_log: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    client = scripted_client(3)
    client.list_failures.extend([RuntimeError(LEAKY), ValueError(LEAKY)])
    client.download_failures.extend([RuntimeError(LEAKY), KeyError(LEAKY), OSError(LEAKY)])
    stack = build(tmp_path, client)
    try:
        link_with_mfa(stack)
        for _ in range(3):
            stack.sync()
        stack.factory.resume_error = RuntimeError(LEAKY)
        stack.sync()
        stack.factory.resume_error = None
        stack.factory.login_error = RuntimeError(LEAKY)
        stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
        )
        stack.factory.login_error = None
    finally:
        stack.close()

    out, err = capfd.readouterr()
    assert not [m for m in MARKERS if m in logged_text(file_log, caplog) + out + err]
    assert files_with_secrets(tmp_path) == []
    assert files_with_secrets(isolated_temp) == []


def test_t_sec_07_a_token_dump_that_fails_with_the_secrets_in_its_text_is_not_logged(
    tmp_path: Path,
    file_log: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    class BrokenDump(ScriptedClient):
        def dump_tokens(self) -> str:
            raise RuntimeError(LEAKY)

    stack = build(
        tmp_path,
        BrokenDump(),
    )
    try:
        result = stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
        )
        assert result is not LoginEnd.CONNECTED
        assert stack.tokens.load(stack.profile_id) is None  # un login fallito non salva nulla
    finally:
        stack.close()

    assert not [m for m in MARKERS if m in logged_text(file_log, caplog)]


def test_t_sec_07_the_unhandled_exception_hooks_and_a_dying_worker_thread_log_types_only(
    tmp_path: Path,
    file_log: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    notified: list[int] = []
    install_exception_hooks(lambda: notified.append(1))

    def die() -> None:
        raise RuntimeError(LEAKY)

    thread = threading.Thread(target=die, name="ponos-garmin-crash")
    thread.start()
    thread.join(10)
    error = ValueError(LEAKY)
    sys.excepthook(ValueError, error, None)

    assert notified == [1, 1]
    assert not [m for m in MARKERS if m in logged_text(file_log, caplog)]


@pytest.mark.parametrize("point", ["download:2", "login"])
def test_t_sec_07_a_real_kill_leaves_no_secret_on_disk(tmp_path: Path, point: str) -> None:
    result = subprocess.run(  # noqa: S603 - interprete e script fissi
        [sys.executable, "-I", str(WORKER), str(tmp_path), point],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 9, result.stderr[-2000:]
    assert files_with_secrets(tmp_path) == []
    assert not [m for m in MARKERS if m in result.stdout + result.stderr]


def test_t_sec_07_the_values_that_can_be_shown_never_contain_the_secrets(tmp_path: Path) -> None:
    stack = build(tmp_path, scripted_client(2))
    try:
        link_with_mfa(stack)
        result = stack.sync()
        tokens = stack.tokens.load(stack.profile_id)
        assert tokens is not None
        shown = " ".join(
            [
                repr(tokens),
                str(tokens),
                f"{tokens}",
                repr(result),
                repr(Credentials(EMAIL, PASSWORD)),
            ]
        )
    finally:
        stack.close()

    assert not [m for m in (TOKEN_MARKER, PASSWORD) if m in shown]
    assert SessionTokens(PLACEHOLDER_TOKENS).reveal() == PLACEHOLDER_TOKENS
    assert set(SECRET_MARKERS) >= {TOKEN_MARKER, PASSWORD}


def test_t_sec_07_a_failed_login_saves_nothing_anywhere(
    tmp_path: Path, isolated_temp: Path
) -> None:
    stack = build(tmp_path, scripted_client(1))
    try:
        stack.factory.login_error = auth_error()
        wrong = stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
        )
        stack.factory.login_error = http_error(500)
        down = stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
        )
        stack.factory.login_error = None
        stack.factory.expected_mfa_code = "altro"
        bad_code = stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, threading.Event()
        )

        assert LoginEnd.CONNECTED not in (wrong, down, bad_code)
        assert stack.tokens.load(stack.profile_id) is None
    finally:
        stack.close()
    assert files_with_secrets(tmp_path) == [] and files_with_secrets(isolated_temp) == []


def test_t_sec_07_a_cancelled_login_saves_nothing(tmp_path: Path) -> None:
    stack = build(tmp_path, scripted_client(1))
    try:
        cancelled = threading.Event()
        cancelled.set()
        result = stack.worker.log_in(
            stack.profile_id, Credentials(EMAIL, PASSWORD), lambda: MFA_CODE, cancelled
        )

        assert result is LoginEnd.CANCELLED
        assert stack.tokens.load(stack.profile_id) is None
    finally:
        stack.close()


# --- Token corrotto o di un altro profilo -----------------------------------------------------


def test_t_sync_04_tokens_that_are_not_a_json_object_never_reach_the_library(
    tmp_path: Path,
) -> None:
    from ponos_coach.ui.sync_model import SyncNotStarted, SyncProblem  # noqa: PLC0415

    stack = build(tmp_path, scripted_client(1))
    try:
        for hostile in (
            r"C:\Windows\win.ini",
            "../../etc/passwd",
            "[1,2]",
            "null",
            "{",
            "\x00" * 10,
        ):
            stack.tokens.save(stack.profile_id, SessionTokens(hostile))
            result = stack.sync()

            assert isinstance(result, SyncNotStarted)
            assert result.problem is SyncProblem.AUTH_REQUIRED
        assert stack.factory.resume_calls == []
    finally:
        stack.close()


def test_t_sync_04_the_tokens_of_another_profile_are_never_used(tmp_path: Path) -> None:
    from ponos_coach.ui.sync_model import SyncNotStarted, SyncProblem  # noqa: PLC0415

    store = InMemoryTokenStore()
    first = build_stack(tmp_path / "a", scripted_client(2), tokens=store)
    second = build_stack(tmp_path / "b", scripted_client(2), tokens=store, linked=False)
    try:
        result = second.sync()  # il secondo profilo non e collegato: i token del primo non contano

        assert isinstance(result, SyncNotStarted) and result.problem is SyncProblem.NOT_LINKED
        assert second.factory.resume_calls == []
        assert first.profile_id != second.profile_id
    finally:
        first.close()
        second.close()


@windows_only
def test_t_sync_04_a_secret_file_copied_to_another_profile_does_not_decrypt(tmp_path: Path) -> None:
    from ponos_coach.ui.sync_model import SyncNotStarted, SyncProblem  # noqa: PLC0415

    store = DpapiTokenStore(tmp_path / "dati")
    first = build_stack(tmp_path / "a", scripted_client(2), tokens=store)
    second = build_stack(tmp_path / "b", scripted_client(2), tokens=store, linked=False)
    try:
        source = next((tmp_path / "dati").rglob(f"{first.profile_id}.bin"))
        (source.parent / f"{second.profile_id}.bin").write_bytes(source.read_bytes())

        result = second.sync()

        assert isinstance(result, SyncNotStarted) and result.problem is SyncProblem.AUTH_REQUIRED
        assert second.factory.resume_calls == []
    finally:
        first.close()
        second.close()


@windows_only
@pytest.mark.parametrize("damage", ["truncate", "flip", "garbage", "empty", "huge"])
def test_t_sync_04_a_damaged_secret_file_asks_for_a_new_login_and_is_replaced(
    tmp_path: Path, damage: str
) -> None:
    from ponos_coach.ui.sync_model import SyncNotStarted, SyncProblem  # noqa: PLC0415

    store = DpapiTokenStore(tmp_path / "dati")
    stack = build_stack(tmp_path, scripted_client(2), tokens=store)
    try:
        secret = next((tmp_path / "dati").rglob("*.bin"))
        data = secret.read_bytes()
        secret.write_bytes(
            {
                "truncate": data[: len(data) // 2],
                "flip": data[:-5] + bytes(b ^ 0xFF for b in data[-5:]),
                "garbage": b"\x00" * 300,
                "empty": b"",
                "huge": data + b"\x00" * 400_000,
            }[damage]
        )

        result = stack.sync()

        assert isinstance(result, SyncNotStarted) and result.problem is SyncProblem.AUTH_REQUIRED
        stack.link()  # un nuovo collegamento sostituisce il file rovinato
        assert isinstance(stack.sync(SyncMode.SYNC), SyncFinished)
    finally:
        stack.close()


# --- T-SEC-08: log della sincronizzazione --------------------------------------------------------


def test_t_sec_08_a_whole_sync_logs_nothing_of_the_user_data(
    tmp_path: Path, file_log: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = scripted_client(5)
    client.download_failures.extend([http_error(500), http_error(503)])
    stack = build(tmp_path, client)
    try:
        link_with_mfa(stack)
        result = stack.sync()
        assert isinstance(result, SyncFinished) and result.outcome.completed
    finally:
        stack.close()

    text = logged_text(file_log, caplog).lower()
    for forbidden in (
        "1000000",
        "1000004",
        "2026-05",
        "bpm",
        "heart",
        "averagehr",
        "atleta di prova",
    ):
        assert forbidden not in text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_t_sec_08_a_hostile_type_name_cannot_forge_a_line_in_the_log_file(
    tmp_path: Path, file_log: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    hostile = type(
        "Boom\n2026-01-01T00:00:00Z CRITICAL ponos_coach FORGED\r\x1b[2J", (Exception,), {}
    )

    class BrokenDump(ScriptedClient):
        def dump_tokens(self) -> str:
            raise hostile("x")

    stack = build_stack(tmp_path, BrokenDump(), tokens=InMemoryTokenStore(), linked=False)
    try:
        stack.tokens.save(stack.profile_id, SessionTokens(PLACEHOLDER_TOKENS))
        stack.sync()
    finally:
        stack.close()

    lines = file_log.read_text(encoding="utf-8").splitlines()
    assert not [
        line for line in lines if line.split(" ", 1)[-1].startswith("CRITICAL ponos_coach FORGED")
    ]
    assert not any("\x1b" in line for line in lines)
