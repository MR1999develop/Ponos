# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 MR
"""`ActivitySource` su Garmin Connect: elenco a pagine e download dell'originale.

Adattatore sottile sopra il "client" (`client.GarminClient`): chiama, traduce gli errori nei tipi
della porta, interpreta la risposta al confine (`listing`) e applica i tetti. I timeout sono
quelli del client (15 s per richiesta); le pause e i ritentativi sono del chiamante, che ha la
politica (`SyncPolicy`): il client non ritenta da solo.

Limite noto: `garminconnect` legge tutto il corpo della risposta in memoria prima di
restituirlo, quindi il tetto di byte del download si applica dopo la lettura e non la
interrompe (il timeout di 15 s e il tetto sulla dimensione dichiarata dall'ZIP a valle
limitano il danno). Idem per la dimensione del JSON dell'elenco.
"""

from __future__ import annotations

import logging

from ponos_coach.activities.domain import MAX_PAGE_SIZE, ActivityPage, GarminActivityId
from ponos_coach.activities.ports import (
    AuthenticationRequiredError,
    DefinitiveSourceError,
    DownloadTooLargeError,
    RateLimitedError,
)
from ponos_coach.garmin.client import GarminClient
from ponos_coach.garmin.errors import translate_source_error
from ponos_coach.garmin.listing import UnexpectedListingError, parse_page, parse_total

_LOGGER = logging.getLogger(__name__)


class GarminActivitySource:
    """Elenca e scarica le attività dell'utente di una sessione già autenticata.

    Non è thread-safe: una sessione per thread di sincronizzazione.
    """

    def __init__(self, client: GarminClient) -> None:
        self._client = client

    def list_page(self, offset: int, limit: int) -> ActivityPage:
        """Vedi `ActivitySource.list_page`.

        Il totale dichiarato si chiede solo per la prima pagina (`offset == 0`): un'altra
        richiesta per pagina raddoppierebbe il carico; se la richiesta del totale fallisce
        per altro che autenticazione o limite di frequenza, il totale resta ignoto.

        Scelta documentata (R12): la richiesta del totale è parte della stessa chiamata
        `list_page` della prima pagina, quindi sta sotto la stessa disciplina del chiamante:
        una sola pausa prima della chiamata, e se Garmin limita la frequenza o chiede il login
        l'errore esce dalla chiamata e il chiamante applica backoff o si ferma. Costa una
        richiesta in più per elenco (non per pagina), senza una seconda pausa fra le due; il
        totale serve solo all'avanzamento e non decide quando l'elenco finisce.

        Raises:
            ValueError: `offset` negativo o `limit` fuori da 1..`MAX_PAGE_SIZE`.
        """
        if offset < 0 or not 1 <= limit <= MAX_PAGE_SIZE:
            raise ValueError("posizione o limite non validi")
        try:
            raw = self._client.list_activities(offset, limit)
        except Exception as exc:
            raise translate_source_error(exc) from exc
        try:
            items, raw_count = parse_page(raw)
        except UnexpectedListingError as exc:
            raise DefinitiveSourceError("risposta dell'elenco non interpretabile") from exc
        if raw_count > len(items):
            _LOGGER.warning("voci dell'elenco scartate", extra={"skipped": raw_count - len(items)})
        total = self._declared_total() if offset == 0 else None
        return ActivityPage(offset, items, raw_count, total)

    def download_original(self, activity_id: GarminActivityId, max_bytes: int) -> bytes:
        """Vedi `ActivitySource.download_original`.

        Raises:
            ValueError: `max_bytes` non positivo.
        """
        if max_bytes <= 0:
            raise ValueError("max_bytes deve essere positivo")
        try:
            content = self._client.download_original(activity_id.value)
        except Exception as exc:
            raise translate_source_error(exc) from exc
        if not isinstance(content, bytes):
            raise DefinitiveSourceError("il download non è un blocco di byte")
        if len(content) > max_bytes:
            raise DownloadTooLargeError("download troppo grande")
        return content

    def _declared_total(self) -> int | None:
        try:
            return parse_total(self._client.count_activities())
        except Exception as exc:
            translated = translate_source_error(exc)
            if isinstance(translated, AuthenticationRequiredError | RateLimitedError):
                raise translated from exc
            return None
