# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Secrets test process: `python -I collaudo_fase2_worker.py <base> <point>`.

Creates a real profile in `<base>/profilo`, links the account (MFA login on a fake client,
placeholder tokens in the platform's real keyring under `<base>/dati`) and synchronises; the
point says where the process dies without cleanup (`os._exit`):

- `download:<n>`: at the n-th download;
- `login`: right after linking, before synchronising;
- `none`: does not die (the `finished` report arrives on stdout).

No network: the client is fake.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # the root, for `tests.*`

from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from ponos_coach.platform.token_store import KeyringTokenStore, create_token_store
from ponos_coach.profiles.ports import TokenStore, TokenStoreUnavailableError
from ponos_coach.ui.sync_worker import Credentials
from tests.collaudo.fase2_support import EMAIL, MFA_CODE, PASSWORD, build_stack, scripted_client
from tests.garmin.scripted import ScriptedClient


class _MemoryKeyring(KeyringBackend):
    """In-memory keyring of the process: the worker uses it where the Secret Service is missing."""

    priority = 1

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}

    def set_password(self, service: str, username: str, password: str) -> None:
        self.items[(service, username)] = password

    def get_password(self, service: str, username: str) -> str | None:
        return self.items.get((service, username))

    def delete_password(self, service: str, username: str) -> None:
        if (service, username) not in self.items:
            raise PasswordDeleteError("assente")
        del self.items[(service, username)]


def _token_store(base: Path) -> TokenStore:
    """The real store (DPAPI on Windows, Secret Service on Linux) if the system offers it.

    On runners without a Secret Service (CI on Ubuntu) an in-memory keyring: the test
    still verifies that no secret ends up on disk.
    """
    try:
        return create_token_store(base)
    except TokenStoreUnavailableError:
        return KeyringTokenStore(_MemoryKeyring())


class _DyingClient(ScriptedClient):
    def __init__(self, die_at: int) -> None:
        inner = scripted_client(4)
        super().__init__(inner._entries, downloads=inner._downloads)
        self._die_at = die_at

    def download_original(self, activity_id: int) -> bytes:
        if self._die_at and len(self.downloaded) + 1 == self._die_at:
            os._exit(9)
        return super().download_original(activity_id)


def main(argv: list[str]) -> int:
    base, point = Path(argv[0]), argv[1]
    die_at = int(point.partition(":")[2]) if point.startswith("download:") else 0
    stack = build_stack(
        base,
        _DyingClient(die_at),
        tokens=_token_store(base / "dati"),
        linked=False,
        folder_name="profilo",
    )
    stack.factory.expected_mfa_code = MFA_CODE
    credentials = Credentials(EMAIL, PASSWORD)
    stack.worker.log_in(stack.profile_id, credentials, lambda: MFA_CODE, threading.Event())
    if point == "login":
        os._exit(9)
    stack.sync()
    print("finished", flush=True)  # noqa: T201 - the dialogue with the test is on stdout
    stack.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
