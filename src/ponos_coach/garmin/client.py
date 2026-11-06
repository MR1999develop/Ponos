# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""Il "client": ciò che il pacchetto chiede a `garminconnect`, come protocollo interno.

La mappatura delle risposte e degli errori si prova con un client finto; la sola classe che
usa davvero la libreria è `connect.ConnectClient`. Le risposte sono JSON non fidato (`object`):
le interpreta chi chiama, al confine. Gli errori sono quelli della libreria, non ancora
tradotti nelle porte.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class GarminClient(Protocol):
    """Una sessione autenticata verso Garmin Connect. Ogni chiamata ha un timeout."""

    def list_activities(self, offset: int, limit: int) -> object:
        """Una pagina dell'elenco delle attività, dalle più recenti, come JSON non interpretato
        (di solito una lista di oggetti)."""
        ...

    def count_activities(self) -> object:
        """Il totale delle attività dichiarato da Garmin, come valore non interpretato."""
        ...

    def download_original(self, activity_id: int) -> bytes:
        """I byte dell'originale (uno ZIP) di un'attività, per id numerico già validato."""
        ...

    def dump_tokens(self) -> str:
        """I token della sessione, serializzati, da salvare nel portachiavi. Segreti."""
        ...


class GarminClientFactory(Protocol):
    """Crea sessioni: con email e password (e MFA) o dai token salvati."""

    def login(self, email: str, password: str, prompt_mfa: Callable[[], str]) -> GarminClient:
        """Accede con le credenziali; se serve l'MFA chiama `prompt_mfa` per il codice.

        Il login non ritenta: gli errori sono quelli della libreria.
        """
        ...

    def resume(self, tokens: str) -> GarminClient:
        """Riprende la sessione da token serializzati (JSON), senza password."""
        ...
