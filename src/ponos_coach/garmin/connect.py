# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""L'unico punto che usa davvero `garminconnect` (0.3.x): client e fabbrica, sottili.

Nessuna logica: ogni metodo è una chiamata alla libreria, di cui lascia passare le eccezioni
(le traduce `errors`). Scelte:

- `retry_attempts=0`: la libreria ritenterebbe da sola 5xx e rete con attese fino a 10 s e il
  chiamante ha già la sua politica (`SyncPolicy`); due livelli di ritentativi si moltiplicano.
- Timeout: quelli della libreria (15 s per richiesta di API e download, 30 s per ogni passo
  del login). Non sono configurabili da qui senza usare parti interne della libreria; per un
  originale molto grande 15 s possono non bastare e l'errore è transitorio.
- Log: `requests`, `urllib3` e `curl_cffi` a WARNING; `garminconnect` a CRITICAL, perché a
  livello ERROR registra con traccia il percorso della richiesta (che contiene l'id
  dell'attività) e il testo degli errori.
- Il login con credenziali passa un archivio di token vuoto (`"{}"`): senza argomento la
  libreria cercherebbe i token nel file indicato dalla variabile d'ambiente `GARMINTOKENS`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Final

from garminconnect import Garmin

from ponos_coach.garmin.client import GarminClient

_QUIET_LOGGERS: Final = {
    "requests": logging.WARNING,
    "urllib3": logging.WARNING,
    "curl_cffi": logging.WARNING,
    "garminconnect": logging.CRITICAL,
}
_EMPTY_TOKENSTORE: Final = "{}"


def quiet_library_loggers() -> None:
    """Porta i logger della libreria e del suo trasporto ai livelli di `_QUIET_LOGGERS`."""
    for name, level in _QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(level)


class ConnectClient:
    """`GarminClient` sopra un'istanza autenticata di `garminconnect.Garmin`."""

    def __init__(self, garmin: Garmin) -> None:
        self._garmin = garmin

    def list_activities(self, offset: int, limit: int) -> object:
        """Vedi `GarminClient.list_activities`."""
        return self._garmin.get_activities(offset, limit)

    def count_activities(self) -> object:
        """Vedi `GarminClient.count_activities`."""
        return self._garmin.count_activities()

    def download_original(self, activity_id: int) -> bytes:
        """Vedi `GarminClient.download_original`."""
        content: bytes = self._garmin.download_activity(
            str(activity_id), dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL
        )
        return content

    def dump_tokens(self) -> str:
        """Vedi `GarminClient.dump_tokens`."""
        tokens: str = self._garmin.client.dumps()
        return tokens


class ConnectClientFactory:
    """`GarminClientFactory` su `garminconnect`: login con MFA e ripresa dai token."""

    def __init__(self) -> None:
        quiet_library_loggers()

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> GarminClient:
        """Vedi `GarminClientFactory.login`."""
        garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa, retry_attempts=0)
        garmin.login(tokenstore=_EMPTY_TOKENSTORE)
        return ConnectClient(garmin)

    def resume(self, tokens: str) -> GarminClient:
        """Vedi `GarminClientFactory.resume`. `tokens` deve essere un oggetto JSON."""
        garmin = Garmin(retry_attempts=0)
        garmin.login(tokenstore=tokens)
        return ConnectClient(garmin)
