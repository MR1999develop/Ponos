# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Processo di prova dei segreti: `python -I collaudo_fase2_worker.py <base> <punto>`.

Crea un profilo vero in `<base>/profilo`, collega l'account (login con MFA su un client finto,
token segnaposto nel portachiavi vero della piattaforma sotto `<base>/dati`) e sincronizza; il
punto dice dove il processo muore senza pulizia (`os._exit`):

- `download:<n>`: al n-esimo download;
- `login`: subito dopo il collegamento, prima di sincronizzare;
- `none`: non muore (il rapporto `finished` arriva su stdout).

Nessuna rete: il client e finto.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # la radice, per `tests.*`

from ponos_coach.platform.token_store import create_token_store
from ponos_coach.ui.sync_worker import Credentials
from tests.collaudo.fase2_support import EMAIL, MFA_CODE, PASSWORD, build_stack, scripted_client
from tests.garmin.scripted import ScriptedClient


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
        tokens=create_token_store(base / "dati"),
        linked=False,
        folder_name="profilo",
    )
    stack.factory.expected_mfa_code = MFA_CODE
    credentials = Credentials(EMAIL, PASSWORD)
    stack.worker.log_in(stack.profile_id, credentials, lambda: MFA_CODE, threading.Event())
    if point == "login":
        os._exit(9)
    stack.sync()
    print("finished", flush=True)  # noqa: T201 - il dialogo col test e su stdout
    stack.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
